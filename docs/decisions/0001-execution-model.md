# ADR-0001: Execution Model

## Status

Accepted, 2026-04-15.

## Context

Commonplace is a multi-phase personal project whose implementation will be carried out largely by AI agents (Claude Code on the Mac mini M1). The plan (`docs/plan.md`) defines *what* to build across six phases; the execution plan (`docs/execution-plan.md`) defines *how* agents carry that out.

Two failure modes are cheap to create and expensive to fix:

1. **Improvised scaffolding.** If the first time the build needs a state file, task contract, or validation gate is mid-execution, an agent invents one. Improvised rails drift in shape, skip gates under pressure, and produce inconsistent state that silently breaks the "hands-off" promise.
2. **Context-window exhaustion.** A single agent holding every file, transcript, and decision across a multi-hour build will run out of usable context and start degrading before the phase finishes.

The scale of the build (six phases, a mix of deterministic plumbing and prompt-iterative synthesis, spanning weekends across an April 2026 Max-plan month) is large enough that neither of these is tolerable.

## Decision

We adopt a **primary-agent / subagent execution model with file-based state coordination**:

- A **primary agent** holds the plan, maintains state, decides what to dispatch, and integrates results. It does not do execution work directly except where spawning a subagent costs more than the task itself.
- **Subagents** receive a focused task contract with explicit inputs, acceptance criteria, validation gates, and stop-and-report conditions. They return a structured 1–2 paragraph summary, not a transcript. Subagents do not modify state, do not spawn further subagents, and do not auto-retry beyond once.
- **State lives in files**, not agent memory: `build/STATE.md` (human view) and `build/state.json` (machine view), at the root of the live working directory. Agents read on start and the primary writes on every state transition.
- **Every task has automated validation gates** (pytest, ruff, smoke checks, LLM-judged samples for synthesis) that must pass before the primary marks it complete.
- **Parallelism is the default**, with a concurrency cap of 5 subagents. Sequential only when there is a real data dependency.
- **Idempotency is non-negotiable**: any task can be safely re-run. Crash-in-the-middle resumes from the last stage-level checkpoint without duplicating work.
- **Model tier follows the work.** Opus for planning/research/heavy reasoning (and the primary agent). Sonnet for standard code-writing subagents. Haiku for mechanical scaffolding and narrow runtime skills. Pick the lowest tier that can do the job well.

Phase 0.0 exists specifically to build this scaffolding (state templates, `AGENTS.md`, Makefile, gates, safe-mode, CI, first ADR, self-test) before any Commonplace functionality. The self-test (task 0.0.15) verifies the loop works end-to-end on a trivial task before we trust it with real ones.

## Consequences

Positive:

- **Hands-off phases become possible.** The human kicks off a phase from their phone, walks away, and returns to either a complete-and-verified phase or a specific blocker with clear context.
- **Context stays clean.** The primary holds state and recent summaries only; subagent transcripts stay in agent-local logs on disk. A phase fits in a single primary session.
- **Parallelism is free when work is independent.** Handlers, tests, and note-generation across books all dispatch concurrently without coordination overhead.
- **Rollback is cheap.** Phase-complete git tags plus database snapshots plus the `safe-mode.sh` panic button mean recovery from a bad state is minutes, not hours.
- **Decisions stay visible.** ADRs capture design rationale; future-agents (or future-human) reading the repo in six months can understand "why" in two minutes.

Negative:

- **Upfront scaffolding cost.** Phase 0.0 is pure rails, no user-facing functionality. The temptation to skip it is high; resisting costs a focused half-session.
- **State file is a single point of contention.** Only the primary writes to it, so a buggy primary corrupts global state. Mitigation: atomic writes, git-backed snapshots, JSON schema validation (future).
- **Verbose task contracts.** Explicit inputs / outputs / gates / budgets / stop-and-reports per task feel heavy for small tasks. The cost is accepted because vague contracts produce vague results, and the whole point of delegation is precision.
- **Model-tier discipline requires attention.** Defaulting to Opus for every subagent is easy and expensive; defaulting to Haiku is cheap and often underpowered. Sonnet-by-default with deliberate up/down moves is the working rule.

## Alternatives considered

- **Single-agent monolithic build.** One Claude session does everything in a long chat. Rejected: context exhaustion, no parallelism, no hands-off.
- **Ad-hoc subagent spawning without state files.** Subagents return results; the primary holds everything in context. Rejected: same context problem, no resumability after a crash.
- **State in a database instead of files.** SQLite for build state. Rejected: overkill for a personal project, makes the state file harder to read at a glance from the phone, harder to version-control meaningfully.

## References

- `docs/plan.md` — Commonplace design (v5)
- `docs/execution-plan.md` — full execution model
- `docs/phase-0-0.md` — this phase's spec
- `AGENTS.md` — 5-minute operating guide at repo root
