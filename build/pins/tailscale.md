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

- **Capture endpoint:** `https://plex-server.tailb9faa9.ts.net/capture` (wired in Phase 1 via `tailscale serve` or `funnel`)
- **MCP:** served alongside capture in the same process

**Note:** Plex Funnel is already active at `https://plex-server.tailb9faa9.ts.net` for the Plex server on this same machine. Phase 1 will need to pick a distinct path prefix or port to avoid collision. Options:

1. Run Commonplace on a non-default port (e.g., `:8765`) and expose via `tailscale serve --https=8765`
2. Use a path like `/cp/capture` while Plex owns `/`
3. Shift Plex to a subpath if its UX allows

Decision deferred to Phase 1 when the capture endpoint lands.

## Existing tailnet entry to review

`100.100.246.51 plex-server-1` — a second macOS node on the tailnet, purpose unknown. May be a stale sign-in from a prior machine or reinstall. Worth auditing via the Tailscale admin console before Phase 1.
