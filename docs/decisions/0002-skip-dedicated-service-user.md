# ADR-0002: Skip the dedicated `commonplace` service user

## Status

Accepted, 2026-04-15. Supersedes the "Restricted user for Claude Code" item in plan v5's Locked Decisions list.

## Context

Plan v5 (single source of truth for the Commonplace design) specifies, under both "Security and privacy" and "Locked decisions":

> Claude Code runs as a dedicated restricted user on the Mac mini with filesystem scope limited to the vault, MCP code, and what's needed.

The intent is defense-in-depth: if Claude Code — or one of its synthesis subagents — misbehaves while running a skill, it cannot read the interactive user's broader filesystem (browser data, shell history, iCloud Drive, other app data). Isolation is enforced by process identity, not by good intentions.

On macOS, implementing this cleanly requires one of:

1. **Relocate code + vault to a shared path** (e.g., `/opt/commonplace/`) owned by the `commonplace` user, with symlinks from the interactive user's home for ergonomics; launchd plists specify `UserName=commonplace`; Claude Code reauthenticates under that user.
2. **Keep current paths, use ACLs** (`chmod +a`) to grant the `commonplace` user access; same launchd + reauth requirements; more fragile because ACLs silently drift under routine file ops.

Both paths cost ~30–60 minutes of setup plus a re-auth of the `claude` CLI in the service user's keychain context, and both add friction to day-to-day workflow (e.g., editing skill files under one user while the worker reads them as another).

## Decision

**Skip the dedicated service user.** Run the MCP server, worker, and all synthesis invocations as the interactive user (`cameronlewis`). Revisit only if the threat model or usage pattern shifts.

The second half of the original locked decision — *"secrets (bearer tokens) in macOS Keychain, not in files Claude Code might scan"* — stands. Keychain-based secret storage is unaffected by this ADR and will be implemented as specified in Phase 1.

## Rationale

The threat model the dedicated user mitigates — "agent goes rogue within my own interactive session on a machine I control" — is narrow on this specific deployment:

- **Network exposure is tailnet-only.** The `/capture` endpoint and MCP server are bound to the private Tailscale tailnet (`tailb9faa9.ts.net`). No public IP, no Funnel on Commonplace ports, no public DNS. An external attacker has no reachable surface.
- **At-rest is FileVault.** Physical theft of the Mac mini yields ciphertext until the login password (now set) is brute-forced.
- **Interactive trust is already total.** The human using this machine is the same human who wrote the skills, runs `claude -p` interactively for other work, and has sudo. A hostile agent running as `cameronlewis` can already do substantial damage regardless of whether the worker also runs as `cameronlewis` vs. `commonplace`.
- **Delivery channel is trusted.** Claude Code itself is installed and auth'd by the human; skills are version-controlled in `commonplace-code`; ingested content is text from known sources (Bluesky, Kindle, YouTube transcripts). There is no "upload arbitrary binary to be executed" path.
- **Operational cost is real.** File ownership splits, ACL drift, two keychain contexts, and launchd-plist complexity add maintenance burden that this personal-scale system cannot amortize.

For a multi-user or customer-facing deployment the calculus flips; this is not that system.

## Consequences

**Accepted:**

- A misbehaving skill or agent could read files outside the vault while running under `cameronlewis`. The healthcheck and skill-file version control are the primary controls against this, not filesystem permissions.
- If Claude Code itself is compromised (e.g., a malicious update), the blast radius is the interactive user's full home directory. Mitigation: `safe-mode.sh` stops services immediately; `launchctl` can be used to kill runaway processes; `git` history is the ground truth for code.
- This ADR is a deliberate deviation from the plan's Locked Decisions list. It must be cited when explaining why the implementation doesn't match that line.

**Un-affected:**

- Secrets in macOS Keychain (Phase 1).
- Tailscale-only network exposure.
- Restricted user isolation would not change any functional behavior; synthesis output, capture latency, and retrieval semantics are identical.

## Revisit conditions

Change this decision if **any** of the following become true:

1. Commonplace starts serving a second user (shared household, partner, etc.).
2. The Mac mini gets a non-tailnet network path (Funnel, public port forward, reverse proxy to the internet).
3. Claude Code or a skill is found to leak filesystem contents outside the vault in a production incident.
4. An ecosystem shift lets us get filesystem sandboxing *without* the dual-user overhead (e.g., a documented `sandbox-exec` profile or Apple App Sandbox for locally-signed binaries becomes practical).

## References

- `docs/plan.md` — "Security and privacy" section, "Locked decisions" section
- `build/pins/tailscale.md` — tailnet facts
- ADR-0001 — Execution model
