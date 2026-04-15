# Phone and iPad Shortcut setup — task 1.9

Step-by-step config for the Android HTTP Shortcut and iPad Apple Shortcut that POST to `/capture`.

## Shared facts

| Field | Value |
|---|---|
| URL | `https://plex-server.tailb9faa9.ts.net:8443/capture` |
| Method | `POST` |
| Header | `Authorization: Bearer <TOKEN>` |
| Header | `Content-Type: application/json` |
| Body | JSON (see schema below) |

**Token** is in macOS keychain at service `commonplace-capture-bearer`, account `capture`. Retrieve with:
```
security find-generic-password -s commonplace-capture-bearer -a capture -w
```
Paste it into each device's shortcut. Do **not** commit the token to git.

**Before testing either shortcut:**
1. `make tailscale-serve` on the Mac mini (one-time; survives reboots).
2. `python -m commonplace_server` running (either manually or as a LaunchAgent once Phase 2 adds that).
3. Phone/iPad must have Tailscale active.

## Body schema

```json
{
  "source": "android-shortcut" | "ipad-shortcut",
  "kind": "text" | "url" | "note",
  "content": "<the captured text or URL>",
  "metadata": {
    "ts_client": "<ISO 8601 timestamp>",
    "app": "<optional source app>"
  }
}
```

`source`, `kind`, `content` are required. `metadata` is optional but useful for debugging.

Expected response on success: HTTP 202 with `{"status":"accepted","job_id":<int>,"inbox_file":"<filename>"}`.

## Android — HTTP Shortcuts app

HTTP Shortcuts by Waboodoo is the recommended app (open-source, Play Store). One shortcut per capture type is clean; a single shortcut with prompt for `kind` is also fine.

Steps:
1. New shortcut → **Method:** POST, **URL:** paste from table above.
2. **Headers tab:** add `Authorization: Bearer <TOKEN>` and `Content-Type: application/json`.
3. **Body tab:** select "Custom text" (JSON). Use a template:
   ```json
   {"source":"android-shortcut","kind":"text","content":"{{input}}","metadata":{"ts_client":"{{timestamp}}"}}
   ```
   — where `{{input}}` is a dynamic variable of type "Text input" prompted on run, and `{{timestamp}}` is a built-in variable for current ISO 8601.
4. **Response handling tab:** show success/error notification so silent failures don't disappear.
5. Add to home screen as a shortcut; also add to the Android share sheet so "Share → Commonplace" works from any app.
6. Test once while on Tailscale — expect toast with `accepted` + `job_id`.

Notes:
- Doze mode can silently drop retries. HTTP Shortcuts has a "retry on failure" option; enable it.
- The shortcut won't work off-tailnet (that's intentional — no public exposure).

## iPad — Apple Shortcuts

Steps:
1. New shortcut → action **Get Contents of URL**.
2. URL: paste from table.
3. Expand **Show More**:
   - **Method:** POST
   - **Headers:** add `Authorization` = `Bearer <TOKEN>`, `Content-Type` = `application/json`
   - **Request Body:** JSON. Add three text fields:
     - `source` = `ipad-shortcut`
     - `kind` = `text` (or make it a magic variable from a prompt)
     - `content` = magic variable from **Ask for Input** (prompt "Capture:")
4. Optionally chain a **Show Notification** action showing the response.
5. Add to share sheet: in the shortcut's details, enable "Show in Share Sheet" and set "Share Sheet Types" to Text + URLs.

## Failure modes to check

| Symptom | Likely cause |
|---|---|
| `Connection refused` | `tailscale serve` is off on Mac mini, or server isn't running |
| `DNS resolution failed` | Tailscale app not connected on phone/iPad |
| `401 Unauthorized` | Token mismatch — re-paste from keychain |
| `503 Service Unavailable` | Server started without `COMMONPLACE_CAPTURE_BEARER` env var and keychain read failed |
| `400 Bad Request` | Missing `source` / `kind` / `content`; malformed JSON |

## When both devices succeed

Reply here with "1.9 done" — task 1.10 (round-trip test) runs next and closes Phase 1.
