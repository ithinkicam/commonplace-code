# ADR-0004: Tailnet exposure scheme for commonplace-server

**Status:** accepted
**Date:** 2026-04-15

## Context

The Mac mini running Commonplace is the same device registered as `plex-server.tailb9faa9.ts.net` on the tailnet. Plex already holds Tailscale Funnel at `https://plex-server.tailb9faa9.ts.net` (port 443) for public exposure, proxying to `127.0.0.1:13378`. The Phase 0 tailscale pin carried a "Plex Funnel collision" forward into Phase 1 to be resolved before `/capture` lands.

Three candidates were considered:
- **(a) Distinct MagicDNS hostname** (e.g., `commonplace.tailb9faa9.ts.net`).
- **(b) Same hostname, non-443 port** (e.g., `:8443` or `:10000`).
- **(c) Move Plex** off the existing Funnel mapping.

### Findings

1. **(a) is not feasible without a second Tailscale device or a device rename.** MagicDNS hostnames are device-level. This tailnet has three nodes (`plex-server`, `pixel-9a`, `plex-server-1`); none of them is `commonplace`. Renaming the Mac mini breaks Plex's existing Funnel URL, which the owner relies on.
2. **Commonplace is tailnet-only per plan v5** ("Private Tailscale tailnet only. No public exposure"). Funnel is therefore not required — the phone and iPad, both tailnet members, can reach the Mac mini directly via tailnet IP or MagicDNS.
3. **Funnel-allowed ports on this tailnet are 443, 8443, 10000.** Only relevant if we ever turn on Funnel; irrelevant for tailnet-only traffic.
4. **(c) is rejected** because Plex works today and reorganising it is out of scope.

## Decision

**Option (b): keep the existing `plex-server.tailb9faa9.ts.net` hostname; put Commonplace on port 8443 via `tailscale serve`.**

- `commonplace-server` binds `127.0.0.1:8765` locally (existing default from task 1.2, configurable via `COMMONPLACE_HOST` / `COMMONPLACE_PORT`).
- Exposure over tailnet uses Tailscale's local HTTPS:
  ```
  tailscale serve --bg --https=8443 --set-path=/ http://127.0.0.1:8765
  ```
  This terminates TLS at the Tailscale daemon using Tailscale's machine cert and proxies to the local server. Clients reach Commonplace at `https://plex-server.tailb9faa9.ts.net:8443/`.
- The capture endpoint (task 1.6) will live at `https://plex-server.tailb9faa9.ts.net:8443/capture`.
- Bearer auth on `/capture` remains a defence-in-depth layer (plan v5's "stored in OS keychains, rotated once after Phase 1 stabilizes").
- `tailscale serve` is configured *but not enabled* until task 1.6 has produced a working endpoint to proxy to.

## Consequences

- **Pros:** no device rename, no collision with Plex, no Funnel exposure, Tailscale handles TLS, single hostname is easier to remember than multiple, reversible (`tailscale serve --https=8443 off`).
- **Cons:** phone and iPad HTTP Shortcuts must include the `:8443` port suffix; forgetting it yields a connection refused. Documented in task 1.9's output.
- **Future:** if Commonplace ever needs public exposure (opposed by v5), Funnel on port 8443 or 10000 is available without further architecture change — swap `serve` for `funnel` at the same port.

## References

- Plan v5, "Private Tailscale tailnet only" section
- `build/pins/tailscale.md`
- Task 1.5 (this ADR is its output)
- Task 1.6 will execute the `tailscale serve` command as part of bringing the capture endpoint online.
