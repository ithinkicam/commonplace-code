# Pinned: Google Drive library + Day One

**Pinned on:** 2026-04-15

## Library location (watched folder)

The Phase 2 library watcher reads local filesystem — Drive for Desktop handles the Drive→disk sync. No Drive API client, no OAuth.

- **Path:** `/Users/cameronlewis/Library/CloudStorage/GoogleDrive-camlewis35@gmail.com/My Drive/books/`
- **Account:** `camlewis35@gmail.com`
- **Size at pin time:** ~100 books (56 epub, 31 pdf, 8 mobi, 5 azw3, 1 chm, plus incidentals)
- **Formats the handler must support:** epub, pdf, mobi, azw3. (chm is a legacy outlier; handle via Calibre conversion or skip.)

## Failure mode to watch

Drive for Desktop occasionally pauses sync when macOS updates or after long sleeps. If the watcher misses files, first check the Drive menu bar icon for "Syncing paused" — not a bug in the watcher.

## Day One

- **App:** `/Applications/Day One.app`, bundle id `com.bloombuilt.dayone-mac`, version `2026.8` (Mac App Store).
- **Ingestion:** NOT ingested into Commonplace corpus. Queried live via the **official Day One MCP** (plan v5 "Design rules" and the data-sources table).
- **MCP wiring:** happens when Claude Code's MCP config gets set up (Phase 1). No Day One CLI is required or used.
- **Journal data location:** Day One keeps local SQLite at `~/Library/Group Containers/.../Day One/`; the MCP reads it directly.

## Day One Backups in Drive

Also present at `My Drive/Day One Backups/` — historical journal exports. Do **not** ingest these; they duplicate what the MCP serves live.
