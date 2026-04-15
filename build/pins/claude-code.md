# Pinned: Claude Code CLI

**Pinned on:** 2026-04-15

- Version: `2.1.109`
- Binary: `/Users/cameronlewis/.local/bin/claude`
- Non-interactive mode: verified. `echo "say exactly OK" | claude -p --model haiku` returned `OK`.

## Rule

**Upgrade deliberately, not automatically.** The worker invokes skills as the runtime synthesis mechanism. A `claude` CLI update can change flag semantics, output format, or default behavior. Plan v5 flags this as a known weakness.

## Skill invocation shape (pinned 2026-04-15)

Plan v5 writes the invocation as `claude -p <skill>`. In practice on 2.1.109, bare `claude -p <skill_name> <json>` loads the full Claude Code context and treats the JSON as a conversational prompt rather than executing the skill. The working non-interactive shape is:

```bash
claude -p --system-prompt-file skills/<skill>/SKILL.md --model haiku "$json_input"
```

Workers and smoke scripts use this form. See `scripts/smoke_classify_book.sh` for the canonical example. If a future Claude Code version re-enables `-p <skill_name>` as a first-class mode, update this note and the worker invocations together — do not silently change one.

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
