"""Read Amazon session cookies from Chrome and store in macOS Keychain.

Uses pycookiecheat to decrypt Chrome's local cookie DB via the user's
Keychain (you'll see a one-time prompt the first time Chrome's encryption
key is read). Writes a JSON array in the shape kindle_scraper expects:
[{"domain": ".amazon.com", "name": "...", "value": "..."}, ...].

Usage:
    python scripts/kindle_cookies_from_chrome.py
"""

from __future__ import annotations

import json
import subprocess
import sys

from pycookiecheat import BrowserType, get_cookies

# Pull cookies for both the marketing/auth domain and the read.amazon.com
# domain that the notebook scraper actually hits.
URLS = [
    "https://www.amazon.com/",
    "https://read.amazon.com/notebook",
]

KEYCHAIN_SERVICE = "commonplace-kindle/session-cookies"
KEYCHAIN_ACCOUNT = "commonplace"


def main() -> int:
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for url in URLS:
        cookies = get_cookies(url, browser=BrowserType.CHROME)
        for name, value in cookies.items():
            # pycookiecheat's get_cookies() returns {name: value}; we don't
            # get domain back per-cookie, so synthesize from the URL host.
            # That's fine for the scraper's amazon.com filter.
            domain = ".amazon.com"
            key = (domain, name)
            if key not in seen and value:
                seen[key] = {"domain": domain, "name": name, "value": value}

    if not seen:
        print("No amazon.com cookies found in Chrome — log in to amazon.com first.", file=sys.stderr)
        return 1

    payload = json.dumps(list(seen.values()))
    subprocess.run(
        [
            "security", "add-generic-password", "-U",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
            "-w", payload,
        ],
        check=True,
    )
    print(f"Stored {len(seen)} Amazon cookies in Keychain ({KEYCHAIN_SERVICE}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
