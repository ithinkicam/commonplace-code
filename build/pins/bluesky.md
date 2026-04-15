# Pinned: Bluesky

**Pinned on:** 2026-04-15

- **Handle:** `ithinkicam.bsky.social`
- **App password keychain item:** `commonplace-bluesky/app-password`
  - Account: `commonplace`
  - Service: `commonplace-bluesky/app-password`
  - Retrieve: `security find-generic-password -a commonplace -s commonplace-bluesky/app-password -w`

## Rotation

App passwords are revocable at https://bsky.app/settings/app-passwords. If rotation is needed:

1. Revoke the old one in the web UI.
2. Generate a new one.
3. `security add-generic-password -U -a commonplace -s commonplace-bluesky/app-password -w '<new>'`
4. Restart the worker (so it re-reads on next authenticate).

## atproto package

- **Version pinned:** `atproto==0.0.65` (latest stable as of 2026-04-15)
- Pinned in `pyproject.toml` under `dependencies`.
- The `Client.login()` / `Client._session` / `Client.get_author_feed()` API is stable across the 0.0.x series at this version.
- If a future version changes these, update the pin in `pyproject.toml` and re-test `test_bluesky_auth.py` + `test_bluesky_handler.py`.

## Rule

**Never write the app password to any file in this repo**, including `state.json`, `STATE.md`, test fixtures, logs, or ADRs. The keychain is the only storage. Handler/worker code reads it at runtime via `security find-generic-password`.
