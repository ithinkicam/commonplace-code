# Pinned: Tailscale + tailnet

**Pinned on:** 2026-04-15

## Install

- **Source:** Mac App Store (NOT Homebrew). Bundle id `io.tailscale.ipn.macos`, version `1.96.2`.
- **Daemon:** runs as system IPNExtension, managed by macOS. No launchd plist to maintain.
- **Why App Store over brew:** unified install channel with Android + iPad (same account, same auto-update path), avoids the tailscaled socket conflict when both are installed, and the CLI inside the app bundle works once shimmed.

## CLI shim

The MAS binary refuses to run via symlink (bundle-identifier check fails). A bash shim that `exec`s the real path works:

```bash
# ~/.local/bin/tailscale
#!/usr/bin/env bash
exec "/Applications/Tailscale.app/Contents/MacOS/Tailscale" "$@"
```

`chmod +x` and make sure `~/.local/bin` is on PATH.

## Tailnet facts

- **Tailnet domain (MagicDNS suffix):** `tailb9faa9.ts.net`
- **Mac mini FQDN:** `plex-server.tailb9faa9.ts.net` (tailnet IP `100.127.200.94`, IPv6 `fd7a:115c:a1e0::8b01:c8be`)
- **Android phone:** `pixel-9a.tailb9faa9.ts.net` (`100.107.34.111`)
- **iPad:** TODO — pending install

## Addresses Commonplace will expose

Resolved in ADR-0004 (`docs/decisions/0004-tailnet-exposure-scheme.md`):

- **Tailnet base:** `https://plex-server.tailb9faa9.ts.net:8443/`
- **Capture endpoint:** `https://plex-server.tailb9faa9.ts.net:8443/capture` (task 1.6)
- **Healthcheck + MCP:** same hostname+port via FastMCP `custom_route`
- **Local bind:** `127.0.0.1:8765` (server default)

**Tailscale serve command — run during task 1.6, not before:**
```
tailscale serve --bg --https=8443 --set-path=/ http://127.0.0.1:8765
```

**Why port 8443:** Plex Funnel already owns port 443 at this hostname. Keeping the same hostname (rather than renaming the device or adding a second Tailscale node) and separating by port is the simplest reversible option. Tailnet-only per plan v5, so Funnel is not used.

## Existing tailnet entry to review

`100.100.246.51 plex-server-1` — a second macOS node on the tailnet, purpose unknown. May be a stale sign-in from a prior machine or reinstall. Worth auditing via the Tailscale admin console before Phase 1.
