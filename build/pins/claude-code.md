# Pinned: Claude Code CLI

**Pinned on:** 2026-04-15

- Version: `2.1.109`
- Binary: `/Users/cameronlewis/.local/bin/claude`
- Non-interactive mode: verified. `echo "say exactly OK" | claude -p --model haiku` returned `OK`.

## Rule

**Upgrade deliberately, not automatically.** The worker invokes `claude -p <skill>` as the runtime synthesis mechanism. A `claude` CLI update can change flag semantics, output format, or default behavior. Plan v5 flags this as a known weakness.

Upgrade procedure:

1. Record new version here before upgrading.
2. Run the full skill-invocation smoke test against the new version.
3. Keep the old version available (e.g., move to `~/.local/bin/claude-<old-version>`) so rollback is a one-line change in the worker.
4. Only after smoke tests pass in staging, point production worker at the new version.

## Smoke

```bash
claude --version
echo "say exactly OK and nothing else" | claude -p --model haiku
# expected: OK
```
