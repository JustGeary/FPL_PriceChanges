#!/usr/bin/env python3
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Dict, Tuple, List
from zoneinfo import ZoneInfo
import requests

FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
ROOT = pathlib.Path(".").resolve()
SNAP_DIR = ROOT / "data" / "snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)

UK_TZ = ZoneInfo("Europe/London")
TELEGRAM_HARD_LIMIT = 4096
SAFE_BUDGET = 3900  # keep a buffer for safety

def fetch_prices() -> Tuple[Dict[int, Tuple[str, int, int]], Dict[int, str]]:
    r = requests.get(FPL_URL, timeout=40)
    r.raise_for_status()
    data = r.json()
    team_short = {int(t["id"]): t["short_name"] for t in data["teams"]}
    players = {int(e["id"]): (e["web_name"], int(e["now_cost"]), int(e["team"])) for e in data["elements"]}
    return players, team_short

def load_latest_snapshot() -> Dict[str, int]:
    snaps = sorted(SNAP_DIR.glob("*.json"))
    if not snaps:
        return {}
    with snaps[-1].open("r", encoding="utf-8") as f:
        return json.load(f)

def save_snapshot(ts_utc: dt.datetime, today_map: Dict[int, Tuple[str, int, int]]) -> pathlib.Path:
    snap_path = SNAP_DIR / f"{ts_utc.date().isoformat()}.json"  # ISO filename for natural sort
    comp = {str(pid): cost for pid, (_, cost, _) in today_map.items()}
    with snap_path.open("w", encoding="utf-8") as f:
        json.dump(comp, f, ensure_ascii=False, separators=(",", ":"))
    return snap_path

def money(tenths: int) -> str:
    return f"£{tenths/10:.1f}m"

def build_lines(risers, fallers) -> List[str]:
    """Return full (untrimmed) list of HTML-formatted lines with headers and bullets."""
    lines: List[str] = []
    if risers:
        lines.append("📈 <b>Risers</b>")
        for ch in risers:
            lines.append(f"• <b>{ch['name']}</b> ({ch['team']}): +{ch['delta']/10:.1f}m → {money(ch['new'])}")
    if fallers:
        if lines:
            lines.append("")  # blank line between groups
        lines.append("📉 <b>Fallers</b>")
        for ch in fallers:
            lines.append(f"• <b>{ch['name']}</b> ({ch['team']}): {ch['delta']/10:.1f}m → {money(ch['new'])}")
    return lines

def main():
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    now_uk = now_utc.astimezone(UK_TZ)
    date_str_uk = now_uk.strftime("%d-%m-%Y")  # dd-MM-YYYY

    players, team_short = fetch_prices()
    prev = load_latest_snapshot()
    save_snapshot(now_utc, players)

    changes = []
    for pid, (name, cost, team_id) in players.items():
        old = prev.get(str(pid))
        if old is not None and old != cost:
            changes.append({"id": pid, "name": name, "team": team_short.get(team_id, ""), "old": old, "new": cost, "delta": cost - old})

    risers = sorted((c for c in changes if c["delta"] > 0), key=lambda x: (-abs(x["delta"]), x["name"].lower()))
    fallers = sorted((c for c in changes if c["delta"] < 0), key=lambda x: (-abs(x["delta"]), x["name"].lower()))
    has_changes = bool(changes)
    header_counts = f"{date_str_uk} (Risers: {len(risers)}, Fallers: {len(fallers)})"

    # ---------- Markdown (full table) ----------
    md_lines = [f"# FPL Price Changes — {header_counts}\n"]
    if not has_changes:
        md_lines.append("_No price changes detected._\n")
    else:
        if risers:
            md_lines += ["## Risers", "| Player | Team | Old | New | Δ |", "|---|:---:|---:|---:|---:|"]
            md_lines += [f"| {c['name']} | {c['team']} | {money(c['old'])} | {money(c['new'])} | +{c['delta']/10:.1f}m |" for c in risers]
            md_lines.append("")
        if fallers:
            md_lines += ["## Fallers", "| Player | Team | Old | New | Δ |", "|---|:---:|---:|---:|---:|"]
            md_lines += [f"| {c['name']} | {c['team']} | {money(c['old'])} | {money(c['new'])} | {c['delta']/10:.1f}m |" for c in fallers]
            md_lines.append("")
        md_lines.append(f"_Total changes: {len(changes)} (Risers: {len(risers)}, Fallers: {len(fallers)})_")
    with open("changes.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    # ---------- Telegram (HTML, dynamic trimming) ----------
    if not has_changes:
        tg = f"<b>FPL Price Changes — {header_counts}</b>\n\nNo changes."
    else:
        head = f"<b>FPL Price Changes — {header_counts}</b>\n\n"
        lines = build_lines(risers, fallers)  # full list
        hidden = 0

        def assemble(lines_list: List[str], hidden_count: int) -> str:
            extra = f"\n\n(+{hidden_count} more)" if hidden_count > 0 else ""
            return head + "\n".join(lines_list) + extra

        # Trim from the end (fallers last) until we fit in SAFE_BUDGET
        while True:
            msg = assemble(lines, hidden)
            if len(msg) <= SAFE_BUDGET or not lines:
                tg = msg
                break
            # Remove the last *non-header* line
            removed = lines.pop()
            # skip blank header separators when counting hidden
            if removed and not removed.startswith(("📈", "📉")):
                hidden += 1
            # If we popped a blank line between groups, pop once more to remove the header above it
            if removed == "" and lines:
                # remove the group header we just separated from
                hdr = lines.pop()
                # don't count header as hidden

    with open("tg_message.txt", "w", encoding="utf-8") as f:
        f.write(tg)

    # ---------- GitHub outputs ----------
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
