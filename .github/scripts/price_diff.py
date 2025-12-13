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


def fetch_prices() -> Tuple[Dict[int, Tuple[str, int, int]], Dict[int, str], Dict[int, float]]:
    """
    Fetch player prices, team short names, and ownership from the FPL API.

    Returns:
        players:    {player_id: (web_name, now_cost, team_id)}
        team_short: {team_id: short_name}
        ownership:  {player_id: selected_by_percent (float)}
    """
    r = requests.get(FPL_URL, timeout=40)
    r.raise_for_status()
    data = r.json()

    team_short = {int(t["id"]): t["short_name"] for t in data["teams"]}

    players: Dict[int, Tuple[str, int, int]] = {}
    ownership: Dict[int, float] = {}

    for e in data["elements"]:
        pid = int(e["id"])
        name = e["web_name"]
        cost = int(e["now_cost"])
        team_id = int(e["team"])

        try:
            selected = float(e.get("selected_by_percent") or 0.0)
        except (TypeError, ValueError):
            selected = 0.0

        players[pid] = (name, cost, team_id)
        ownership[pid] = selected

    return players, team_short, ownership


def fetch_current_gw() -> int | None:
    """Return the current gameweek id from FPL API, or None if not found."""
    r = requests.get(FPL_URL, timeout=40)
    r.raise_for_status()
    data = r.json()
    for ev in data.get("events", []):
        if ev.get("is_current"):
            try:
                return int(ev["id"])
            except (TypeError, ValueError):
                return None
    return None


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
    """Return full (untrimmed) list of HTML-formatted lines with headers and bullets for Telegram."""
    lines: List[str] = []
    if risers:
        lines.append("📈 <b>Risers</b>")
        for ch in risers:
            lines.append(
                f"• <b>{ch['name']}</b> ({ch['team']}): +{ch['delta']/10:.1f}m → {money(ch['new'])}"
            )
    if fallers:
        if lines:
            lines.append("")  # blank line between groups
        lines.append("📉 <b>Fallers</b>")
        for ch in fallers:
            lines.append(
                f"• <b>{ch['name']}</b> ({ch['team']}): {ch['delta']/10:.1f}m → {money(ch['new'])}"
            )
    return lines


def build_x_chunks(
    header_text: str,
    title: str,
    emoji: str,
    items,
    max_len: int = 255,  # <— tuned to avoid X "soft cap" 403s
) -> List[str]:
    """
    Build one or more X messages (chunks) for either Risers or Fallers.

    Each chunk is <= max_len characters. If there are many players, they are
    split across multiple tweets, which can later be posted as a thread.
    """
    chunks: List[str] = []
    if not items:
        return chunks

    def text_len(lines: List[str]) -> int:
        return len("\n".join(lines).rstrip())

    # First chunk: header + blank + section title
    current_lines: List[str] = [header_text, "", f"{emoji} {title}:"]

    for c in items:
        bullet = f"• {c['name']} ({c['team']}) {money(c['new'])}"
        candidate = current_lines + [bullet]
        if text_len(candidate) <= max_len:
            current_lines = candidate
        else:
            # Finalise current chunk
            chunks.append("\n".join(current_lines).rstrip())
            # Subsequent chunks: lighter header
            cont_header = f"{emoji} {title} (cont.)"
            current_lines = [cont_header, bullet]

    if current_lines:
        chunks.append("\n".join(current_lines).rstrip())

    return chunks


def main():
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    now_uk = now_utc.astimezone(UK_TZ)
    date_str_uk = now_uk.strftime("%d-%m-%Y")  # dd-MM-YYYY

    players, team_short, ownership = fetch_prices()
    prev = load_latest_snapshot()
    save_snapshot(now_utc, players)

    changes = []
    for pid, (name, cost, team_id) in players.items():
        old = prev.get(str(pid))
        if old is not None and old != cost:
            changes.append(
                {
                    "id": pid,
                    "name": name,
                    "team": team_short.get(team_id, ""),
                    "old": old,
                    "new": cost,
                    "delta": cost - old,
                    "ownership": ownership.get(pid, 0.0),
                }
            )

    # Sort by highest ownership first, then name
    risers = sorted(
        (c for c in changes if c["delta"] > 0),
        key=lambda x: (-x.get("ownership", 0.0), x["name"].lower()),
    )
    fallers = sorted(
        (c for c in changes if c["delta"] < 0),
        key=lambda x: (-x.get("ownership", 0.0), x["name"].lower()),
    )
    has_changes = bool(changes)

    # Gameweek + header text for MD/Telegram
    gw = fetch_current_gw()
    if gw is not None:
        header_prefix = f"GW{gw} — {date_str_uk}"
    else:
        header_prefix = date_str_uk

    header_counts = f"{header_prefix} (Risers: {len(risers)}, Fallers: {len(fallers)})"

    # ---------- Markdown (full table) ----------
    md_lines = [f"# FPL Price Changes — {header_counts}\n"]
    if not has_changes:
        md_lines.append("_No price changes detected._\n")
    else:
        if risers:
            md_lines += [
                "## Risers",
                "| Player | Team | Old | New | Δ | Own% |",
                "|---|:---:|---:|---:|---:|---:|",
            ]
            md_lines += [
                f"| {c['name']} | {c['team']} | {money(c['old'])} | {money(c['new'])} | +{c['delta']/10:.1f}m | {c.get('ownership', 0.0):.1f}% |"
                for c in risers
            ]
            md_lines.append("")
        if fallers:
            md_lines += [
                "## Fallers",
                "| Player | Team | Old | New | Δ | Own% |",
                "|---|:---:|---:|---:|---:|---:|",
            ]
            md_lines += [
                f"| {c['name']} | {c['team']} | {money(c['old'])} | {money(c['new'])} | {c['delta']/10:.1f}m | {c.get('ownership', 0.0):.1f}% |"
                for c in fallers
            ]
            md_lines.append("")
        md_lines.append(
            f"_Total changes: {len(changes)} (Risers: {len(risers)}, Fallers: {len(fallers)})_"
        )
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

        while True:
            msg = assemble(lines, hidden)
            if len(msg) <= SAFE_BUDGET or not lines:
                tg = msg
                break
            removed = lines.pop()
            if removed and not removed.startswith(("📈", "📉")):
                hidden += 1
            if removed == "" and lines:
                _hdr = lines.pop()

    with open("tg_message.txt", "w", encoding="utf-8") as f:
        f.write(tg)

    # ---------- X status (plain text, split into threaded chunks) ----------
    if gw is not None:
        header_risers = f"📈 FPL Risers GW{gw}\n📅 {date_str_uk} (R:{len(risers)}) #FPL"
        header_fallers = f"📉 FPL Fallers GW{gw}\n📅 {date_str_uk} (F:{len(fallers)}) #FPL"
    else:
        header_risers = f"📈 FPL Risers\n📅 {date_str_uk} (R:{len(risers)}) #FPL"
        header_fallers = f"📉 FPL Fallers\n📅 {date_str_uk} (F:{len(fallers)}) #FPL"

    riser_chunks = build_x_chunks(header_risers, "Risers", "📈", risers)
    faller_chunks = build_x_chunks(header_fallers, "Fallers", "📉", fallers)

    for idx, msg in enumerate(riser_chunks, start=1):
        with open(f"x_status_risers_{idx}.txt", "w", encoding="utf-8") as f:
            f.write(msg)

    for idx, msg in enumerate(faller_chunks, start=1):
        with open(f"x_status_fallers_{idx}.txt", "w", encoding="utf-8") as f:
            f.write(msg)

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
