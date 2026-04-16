# Profile Regeneration

The profile-regen pipeline regenerates `~/commonplace/profile/current.md` — the
tier-3 operational profile loaded at chat start — from a sample of recent corpus
signal, the stable perennials (tier 2), and any cross-chat additions queued in
the profile inbox.

## What the handler does

`commonplace_worker/handlers/profile.py` implements the `regenerate_profile` job kind.

**Steps (in order):**

1. Read `~/commonplace/profile/perennials.md` (required; fails with a clear error if missing).
2. Read `~/commonplace/profile/current.md` if it exists; empty string on first run (cold start).
3. Collect inbox additions from `~/commonplace/profile/inbox/*.md` — each file must have a `timestamp: ISO8601` line in its YAML frontmatter. If the frontmatter is missing, the file's mtime is used as a fallback timestamp.
4. Sample corpus signal from the DB (no embedding search — sampled directly):
   - Recent 20 Kindle highlights (`content_type IN ('kindle', 'kindle_highlight')`)
   - Recent 10 captures (`content_type IN ('article', 'youtube', 'podcast', 'image', 'video')`)
   - Recent 15 Bluesky posts (`content_type='bluesky'`)
   - Up to 10 book/audiobook titles engaged in the last 90 days
   - Each snippet is the first chunk (`chunk_index=0`), truncated to 300 chars
5. Invoke `claude -p --system-prompt-file skills/regenerate_profile/SKILL.md --model opus` with the JSON payload. Timeout: 10 minutes.
6. Validate the output via `skills/regenerate_profile/parser.py`. If validation fails or any `[directive, YYYY-MM-DD]` line from the input is missing from the output, the job fails and the previous `current.md` is left intact.
7. Snapshot the old `current.md` to `~/commonplace/profile/history/current-YYYY-MM-DDTHH-MM-SSZ.md`.
8. Atomically write the new `current.md` via `.tmp` + fsync + rename.
9. Move processed inbox files to `~/commonplace/profile/inbox/processed/`.

## When the cron fires

`scripts/com.commonplace.profile-regen.plist` is a launchd user agent that fires
**monthly on the first of the month at 03:00 local time**. It calls
`scripts/submit_profile_regen.py` which enqueues a `regenerate_profile` job in
the worker queue. The worker picks it up on its next poll and runs the full
pipeline above.

Install (once):

```bash
cp scripts/com.commonplace.profile-regen.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.commonplace.profile-regen.plist
```

## How to run manually

```bash
# From the repo root with the venv active:
python scripts/submit_profile_regen.py
```

This enqueues a job immediately. The worker will pick it up within its polling
interval (default 1 second). Logs go to stderr (worker) and
`~/Library/Logs/commonplace-worker.err.log`.

Dry run (no job enqueued):

```bash
python scripts/submit_profile_regen.py --dry-run
```

## How to verify

Check job status from a Python REPL:

```python
from commonplace_db.db import connect, migrate
from commonplace_server.jobs import status

conn = connect()
migrate(conn)
print(status(conn, <job_id>))
```

Or query the DB directly:

```sql
SELECT id, kind, status, started_at, completed_at, error
FROM job_queue
WHERE kind = 'regenerate_profile'
ORDER BY created_at DESC
LIMIT 5;
```

After a successful run, `~/commonplace/profile/current.md` will have a fresh
`# Profile — updated YYYY-MM-DD` header and the previous version will be in
`~/commonplace/profile/history/`.

## Inbox additions format

Any `.md` file dropped into `~/commonplace/profile/inbox/` is consumed on the
next regen. Expected frontmatter:

```markdown
---
timestamp: 2026-04-15T10:00:00Z
---

The text of the addition.
```

If the frontmatter is absent, the entire file content becomes the addition body
and the file's modification time is used as the timestamp. Processed files are
moved to `~/commonplace/profile/inbox/processed/` so they are not re-consumed.
