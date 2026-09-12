# FPL timing and recovery

Primary trigger: Cyberdyne at **00:05 UK local time**. Completion check: **00:15 UK**.
Both Windows tasks run as SYSTEM, wake the machine if supported, and catch up after
a missed start. Cyberdyne must have power and internet; wake cannot start a powered-off PC.
The existing GitHub cron at 02:45 UTC remains an independent, best-effort fallback.

The workflow requests two matching changed FPL responses a minute apart. It logs
request/response timing and cache headers. Early unchanged results are polled until
approximately 00:12 (nine requests maximum). Later runs try three times. This is
evidence of stability, not a guarantee that FPL has finished every update.

Snapshots use Europe/London dates. Each day's prepared messages and report are
committed under `data/delivery/YYYY-MM-DD.json` before any notifications. Each POST
has a durable `sending` marker before it, and a durable receipt after it. A complete
ledger makes all later runs for that date a no-op. Workflow concurrency serializes
delivery and completion checks, and checkout reads the current main branch.

Definite HTTP rejections remain retryable by a fresh normal workflow dispatch or the
fallback; successful messages are skipped and X replies use stored parent IDs.
Timeouts, 5xx responses, crashes during sending, or receipt persistence failures are
held for human review: inspect the destination, then set the record to `sent` with
the actual ID, or to `ready` only after confirming nothing was published. Never
reset the whole ledger to retry. Prior unresolved deliveries block new days and
must be reviewed rather than discarded. There is no claim of exactly-once delivery
across an external API and GitHub; uncertain outcomes intentionally stop automation.

A day with no changed prices remains `no_change_unconfirmed`; it is rechecked by
the fallback and reported by the completion check. Its observed snapshot is kept
for tomorrow, but it does not falsely claim FPL publication was confirmed. A missing
yesterday snapshot or incomplete API response requires attention.

The 2026-09-12 ledger is a migration marker for the already verified Telegram run
and successful three-post X recovery. It prevents deployment tests reposting today.

## Windows installation

Run `Install-FplSchedule.ps1` in elevated Windows PowerShell **as Steve**, who has
the saved JustGeary Git Credential Manager login. It installs:

- `FPL Price Bot - Trigger` at 00:05
- `FPL Price Bot - Check` at 00:15
- `FPL Price Bot - Probe`, an on-demand read-only authentication test

The installer copies the runtime to `D:\Services\FPL-Scheduler`, locks that folder
to Steve, SYSTEM and Administrators, and stores a machine-DPAPI-encrypted copy of
the existing GitHub token. Its original GitHub permissions are retained; no X or
Telegram secrets leave GitHub. Renew/reinstall if that GitHub token is revoked.
The PC timezone must remain `GMT Standard Time` (UK), which follows BST/GMT.

The probe verifies the SYSTEM account can decrypt the credential and read the
active workflow. After installation, inspect the probe's LastTaskResult and
`D:\Services\FPL-Scheduler\logs\YYYY-MM-DD-Probe.json`.

The completion task dispatches a read-only `mode=check` GitHub run, whose failure is
visible in Actions and eligible for the account's configured workflow notifications.
It also checks the ledger directly, writes a local daily JSON status, and on failure
writes Application event source `FPLPriceBot`, event 1001, and exits nonzero. Local
dispatch errors are also logged this way. No new email/Telegram alert subscription
is installed. Enable GitHub Actions failure notifications in the account if wanted.

## Validation and rollback

Run `python -m unittest discover -s tests -v` with requests, requests-oauthlib and
tzdata installed on Windows. Tests mock all notification traffic.

Test the cloud deployment using today's complete ledger: a normal run must say
`Already delivered`, and a check run must pass without fetching FPL or posting.
The first live early-night run still needs observation to validate the GitHub cache
route. Inspect its fetch logs, receipts and the 00:15 result.

To stop the primary trigger, disable the two Windows daily tasks. The GitHub
fallback continues. Do not restore the old posting workflow without considering
the daily ledger, because the old code does not honor notification receipts.
