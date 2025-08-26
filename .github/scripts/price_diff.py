#!/usr/bin/env python3
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Dict, Tuple
from zoneinfo import ZoneInfo

import requests

FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
ROOT = pathlib.Path(".").resolve()
SNAP_DIR = ROOT / "data" / "snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)

UK_TZ = ZoneInfo("Europe/London")

def fetch_prices() -> Tuple[Dict[int, Tuple[str, int, int]], Dict[int, str]]:
    """
    Returns:
      players: {player_id: (web_name, now_cost, team_id)}
      team_short: {team_id: short_name}  # e.g., 1: "ARS"
    """
    r = requests.get(FPL_URL, timeout=40)
    r.raise_for_status()
    data = r.json()

    team_short = {int(t["id"]): t["short_name"] for t in data["teams"]}
    players = {}
    for e in data["elements"]:
        players[int(e["id"])] = (e["web_name"], int(e["now_cost"]), int(e["team"]))
    return players, team_short

def load_latest_snapshot() -> Dict[str, int]:
    snaps = sorted(SNAP_DIR.glob("*.json"))
    if not snaps:
        return {}
    with snaps[-1].open("r", encoding="utf-8") as f:
        return json.load(f)

def save_snapshot(ts_utc: dt.datetime, today_map: Dict[int, Tuple[str, int, int]]) -> pathlib.Path:
    # Store as {id: now_cost} using ISO filename for natural sort
    snap_path = SNAP_DIR / f"{ts_utc.date().isoformat()}.json"
    comp = {str(pid): cost for pid, (_, cost, _) in today_map.items()}
    with snap_path.open("w", encoding="utf-8") as f:
        json.dump(comp, f, ensure_ascii=False, separators=(",", ":"))
    return snap_path

def money(tenths: int) -> str:
    return f"£{tenths/10:.1f}m"

def main():
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    now_uk = now_utc.astimezone(UK_TZ)
    # Use dd-MM-YYYY (dash) so it's valid for artifact names and looks UK-style
    date_str_uk = now_uk.strftime("%d-%m-%Y")

    players, team_short = fetch_prices()
    prev = load_latest_snapshot()            # {id: cost}
    save_snapshot(now_utc, players)

    # Build change list
    changes = []  # dicts: id, name, team_code, old, new, delta
    for pid, (name, cost, team_id) in players.items():
        old = prev.get(str(pid))
        if old is not None and old != cost:
            changes.append({
                "id": pid,
                "name": name,
                "team": team_short.get(team_id, ""),
                "old": old,
                "new": cost,
                "delta": cost - old
            })

    # Group: risers (delta>0) first, then fallers (delta<0)
    risers = [c for c in changes if c["delta"] > 0]
    fallers = [c for c in changes if c["delta"] < 0]

    # Sort within groups: biggest absolute move, then name
    risers.sort(key=lambda x: (-abs(x["delta"]), x["name"].lower()))
    fallers.sort(key=lambda x: (-abs(x["delta"]), x["name"].lower()))

    has_changes = bool(changes)
    header_counts = f"{date_str_uk} (Risers: {len(risers)}, Fallers: {len(fallers)})"

    # ------- Write detailed markdown (changes.md) -------
    md_lines = [f"# FPL Price Changes — {header_counts}\n"]
    if not has_changes:
        md_lines.append("_No price changes detected._\n")
    else:
        if risers:
            md_lines.append("## Risers\n")
            md_lines.append("| Player | Team | Old | New | Δ |")
            md_lines.append("|---|:---:|---:|---:|---:|")
            for ch in risers:
                md_lines.append(
                    f"| {ch['name']} | {ch['team']} | {money(ch['old'])} | {money(ch['new'])} | +{ch['delta']/10:.1f}m |"
                )
            md_lines.append("")  # blank line

        if fallers:
            md_lines.append("## Fallers\n")
            md_lines.append("| Player | Team | Old | New | Δ |")
            md_lines.append("|---|:---:|---:|---:|---:|")
            for ch in fallers:
                md_lines.append(
                    f"| {ch['name']} | {ch['team']} | {money(ch['old'])} | {money(ch['new'])} | {ch['delta']/10:.1f}m |"
                )
            md_lines.append("")

        md_lines.append(f"_Total changes: {len(changes)} (Risers: {len(risers)}, Fallers: {len(fallers)})_")

    with open("changes.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    # ------- Build HTML-formatted Telegram message (tg_message.txt) -------
    if not has_changes:
        tg = f"<b>FPL Price Changes — {header_counts}</b>\n\nNo changes."
    else:
        head = f"<b>FPL Price Changes — {header_counts}</b>\n\n"
        lines = []

        MAX_LINES = 25
        max_risers = min(len(risers), MAX_LINES // 2 if fallers else MAX_LINES)
        max_fallers = min(len(fallers), MAX_LINES - max_risers)

        if risers:
            lines.append("📈 <b>Risers</b>")
            for ch in risers[:max_risers]:
                lines.append(f"• <b>{ch['name']}</b> ({ch['team']}): +{ch['delta']/10:.1f}m → {money(ch['new'])}")

        if fallers:
            if lines:
                lines.append("")  # blank line between groups
            lines.append("📉 <b>Fallers</b>")
            for ch in fallers[:max_fallers]:
                lines.append(f"• <b>{ch['name']}</b> ({ch['team']}): {ch['delta']/10:.1f}m → {money(ch['new'])}")

        truncated = (len(risers) > max_risers) or (len(fallers) > max_fallers)
        if truncated:
            hidden = (len(risers) - max_risers) + (len(fallers) - max_fallers)
            lines.append(f"\n(+{hidden} more)")

        tg = head + "\n".join(lines)

    with open("tg_message.txt", "w", encoding="utf-8") as f:
        f.write(tg)

    # ------- Set GitHub outputs -------
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"has_changes={'true' if has_changes else 'false'}\n")
            f.write(f"date={date_str_uk}\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
