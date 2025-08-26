#!/usr/bin/env python3
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Dict, Tuple
import requests

FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
ROOT = pathlib.Path(".").resolve()
SNAP_DIR = ROOT / "data" / "snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)

def fetch_prices() -> Dict[int, Tuple[str, int]]:
    """Return {player_id: (web_name, now_cost)}"""
    r = requests.get(FPL_URL, timeout=40)
    r.raise_for_status()
    data = r.json()
    players = {}
    for e in data["elements"]:
        players[int(e["id"])] = (e["web_name"], int(e["now_cost"]))
    return players

def load_latest_snapshot() -> Dict[str, int]:
    snaps = sorted(SNAP_DIR.glob("*.json"))
    if not snaps:
        return {}
    with snaps[-1].open("r", encoding="utf-8") as f:
        return json.load(f)

def save_snapshot(ts: dt.datetime, today_map: Dict[int, Tuple[str, int]]) -> pathlib.Path:
    snap_path = SNAP_DIR / f"{ts.date().isoformat()}.json"
    # Store as {id: now_cost}
    comp = {str(pid): cost for pid, (_, cost) in today_map.items()}
    with snap_path.open("w", encoding="utf-8") as f:
        json.dump(comp, f, ensure_ascii=False, separators=(",", ":"))
    return snap_path

def format_money(tenths: int) -> str:
    return f"£{tenths/10:.1f}m"

def main():
    now_utc = dt.datetime.utcnow()
    today = fetch_prices()  # {id: (name, cost)}
    prev = load_latest_snapshot()  # {id: cost}
    save_snapshot(now_utc, today)

    changes = []  # list of dicts with id, name, old, new, delta
    for pid, (name, cost) in today.items():
        old = prev.get(str(pid))
        if old is not None and old != cost:
            changes.append({
                "id": pid,
                "name": name,
                "old": old,
                "new": cost,
                "delta": cost - old
            })

    # Sort biggest movers first (then name)
    changes.sort(key=lambda x: (-abs(x["delta"]), x["name"].lower()))

    has_changes = bool(changes)

    # Write detailed markdown
    md_lines = []
    md_lines.append(f"# FPL Price Changes — {now_utc.date().isoformat()} (UTC)\n")
    if not has_changes:
        md_lines.append("_No price changes detected._\n")
    else:
        md_lines.append("| Player | Old | New | Δ |")
        md_lines.append("|---|---:|---:|---:|")
        for ch in changes:
            md_lines.append(
                f"| {ch['name']} | {format_money(ch['old'])} | {format_money(ch['new'])} | "
                f"{'+' if ch['delta']>0 else ''}{ch['delta']/10:.1f}m |"
            )
        md_lines.append(f"\n_Total changes: {len(changes)}_")
    with open("changes.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    # Build compact Telegram message (keep well under 4096 chars)
    if not has_changes:
        tg = f"FPL Price Changes — {now_utc.date().isoformat()}:\nNo changes."
    else:
        head = f"FPL Price Changes — {now_utc.date().isoformat()}:\n"
        # Show up to 25 lines concisely; the rest count summarized
        lines = []
        MAX_LINES = 25
        for i, ch in enumerate(changes[:MAX_LINES], 1):
            sign = "+" if ch["delta"] > 0 else ""
            lines.append(f"{i}. {ch['name']}: {sign}{ch['delta']/10:.1f}m → {format_money(ch['new'])}")
        extra = ""
        if len(changes) > MAX_LINES:
            extra = f"\n(+{len(changes) - MAX_LINES} more)"
        tg = head + "\n".join(lines) + extra

    with open("tg_message.txt", "w", encoding="utf-8") as f:
        f.write(tg)

    # Set GitHub outputs
    # In GitHub Actions, write to the file path in the GITHUB_OUTPUT env var.
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"has_changes={'true' if has_changes else 'false'}\n")
            f.write(f"date={now_utc.date().isoformat()}\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
