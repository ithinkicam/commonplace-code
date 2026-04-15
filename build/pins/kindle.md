# Pinned: Kindle Highlights Scraper

**Pinned on:** 2026-04-15

## Keychain item

- **Service:** `commonplace-kindle/session-cookies`
- **Account:** `commonplace`
- **Value:** Full JSON array of cookie objects exported by the user's browser extension (e.g., EditThisCookie, Cookie-Editor)
- **Retrieve at runtime:** `security find-generic-password -a commonplace -s commonplace-kindle/session-cookies -w`

## Install cookies (primary uses this after user exports)

```bash
make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json
```

This reads the JSON file, loads it into keychain, and deletes the source file.

## URL structure (as of 2026-04-15)

- **Notebook landing page:** `https://read.amazon.com/notebook`
  - Lists all books that have highlights. Each book card has an `asin` attribute.
  - Book data: title, author, cover image URL.
- **Per-book highlights:** `https://read.amazon.com/notebook?asin=<ASIN>&contentLimitState=&`
  - Lists all highlights + notes for a given ASIN.
  - Each highlight has: location, page, text, optional note, color, and created timestamp.

## Scraper library + version pinned

- **httpx** `>=0.27,<1` — already in project dependencies. Used for HTTP requests with cookie auth.
- **beautifulsoup4** `==4.14.3` — already in project dependencies. Used for HTML parsing.
- **lxml** `>=6.0,<7` — already in project dependencies. Used as BS4 parser backend.

No new library dependencies required — all parsing libraries are already pinned in `pyproject.toml`.

## Selector versioning

`KINDLE_SCRAPER_SELECTORS_VERSION = "2026-04-15"` in `commonplace_worker/kindle_scraper.py`.

Any future selector breakage will raise `KindleStructureChanged` with the failing selector name, and write an alert file to `~/commonplace/alerts/kindle-broken-YYYY-MM-DD.txt`.

## Known failure modes

1. **Session rot** — Amazon session cookies expire (typically 1–7 days). Symptom: scraper gets a login redirect page instead of notebook content. Mitigation: user re-exports cookies via browser extension, runs `make kindle-cookies-install`. Handler surfaces `blocked_on_session_rot` with cookie names that appear missing.

2. **HTML structure changes** — Amazon periodically renames CSS classes, reorganizes DOM, or changes ASIN attribute locations. Symptom: `KindleStructureChanged` exception. Mitigation: version-pinned selectors make the breakage visible immediately; alerts written to `~/commonplace/alerts/`; Readwise is the documented escape hatch if this happens more than twice in six months (plan v5).

3. **2FA re-auth** — If Amazon detects unusual login activity, it may require 2FA even with valid session cookies. Symptom: redirect to `/ap/signin` or `/ap/cvf/`. Mitigation: user must sign in via browser (establishing fresh session), then re-export cookies.

4. **Rate limiting / CAPTCHA** — Aggressive scraping may trigger 503 or CAPTCHA pages. Mitigation: 1.5s minimum delay between requests (jittered), 200-request cap per run.

5. **>200 books** — If the user has more books than the per-run request cap allows, the scraper surfaces this to primary rather than silently truncating. Increase cap or run incrementally with `--book <asin>`.

## Rule

**Never write cookie values to any file in this repo**, including state files, test fixtures, logs, or this document. The keychain is the only storage.
