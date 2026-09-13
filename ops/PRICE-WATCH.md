# Telegram prediction trial: 13–19 September 2026

Cyberdyne dispatches `price-watch.yml` every 30 minutes from 08:00 through 23:00 UK.
Only newly observed current progress at >= +100% or <= -100% generates a message.
All qualifying players are grouped by rise/fall; nothing is sent when there are no
new candidates. The first run includes players already over the threshold.

At 23:30 the roundup groups each eligible player once: current threshold reached,
otherwise projected threshold reached, otherwise current/projected within 90–100%.
Previously alerted threshold players are marked. The first projection (`offset=0`)
is used for tonight; later nights' projections never trigger tonight's warning.
Locked, removed and calibrating players are excluded. Predictions are labelled as
uncertain. Freshness checks reject prediction timestamps older than 45 minutes and
deadlines that do not match the next UK midnight.

One alert per player/direction/deadline is reserved in the ledger before sending.
Re-crossing does not alert again; the next night's ledger allows a new warning.
The roundup is a deliberate nightly summary, not another threshold alert.
Digests exceeding Telegram's limit are split into numbered grouped parts, with
receipts for each part. Timeouts/uncertain delivery are held for review. Definite
rejections can resume for 45 minutes; older batches are retained without stale
resends. Inspect any failed Action before manually changing delivery status.

The cloud workflow shares the confirmed-delivery concurrency lock to prevent
competing repository commits. It receives only Telegram secrets, never X secrets.
It does not change confirmed-price snapshots, the normal bot or its schedule.

Each poll records all player prices/progress/projections under `data/price-watch`.
The ledger preserves exact notification text and Telegram message IDs. `review.json`
compares nightly roundup projections and early alerts against next-day snapshots,
listing correct predictions, misses and predictions that did not happen. Snapshot
comparisons are observations, not a proof of the underlying price algorithm.

Install `Install-PriceWatch.ps1` with administrator rights; it uses the existing
protected scheduler GitHub credential and runs tasks as SYSTEM. Trial triggers end
at 00:00 UK on 20 September. Both local and cloud date guards stop later sends.
An additional read-only review runs on 20 September at 08:00, then expires. The
workflow stays available for inspecting artifacts; extending the trial needs an
explicit change to its date limits.

Preview without sending: `python .github/scripts/price_watch.py --mode preview`.
Run mocked tests with `python -m unittest discover -s tests -v`.

## Morning Telegram accuracy report

`Install-PriceWatchMorning.ps1` adds an unattended 08:00 UK daily task from
14–20 September inclusive. It sends one grouped report for the previous night's
23:30 roundup. It scores the categories actually displayed: current threshold
reached takes precedence, then projected threshold reached. Close-only players
are excluded from firm predictions and labelled "on watch" among missed changes.
Wrong-direction moves count as both a false prediction and a missed actual move.

The report includes totals and names for false alarms and misses, and separately
scores delivered early threshold alerts, listing any early false alarms. A
complete confirmed-delivery ledger, complete snapshot and fully delivered roundup
are required; otherwise a pending notice is sent without declaring errors.
Nightly receipts prevent repeat reports. A manual morning rerun after evidence
arrives can send one completed follow-up to a pending notice; it cannot repeat
either report. Ambiguous Telegram results are held for review as with alerts.
All morning sends are date-limited through 20 September, including delayed jobs.
