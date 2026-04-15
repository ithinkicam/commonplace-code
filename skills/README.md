# Skills

Synthesis and judgment prompts invoked by the worker via `claude -p <skill>`. Each skill is a directory containing a `SKILL.md` file.

## Format

Each skill directory looks like:

```
skills/
└── <skill_name>/
    └── SKILL.md
```

`SKILL.md` has frontmatter pinning the model and describing the skill, followed by the prompt body.

```markdown
---
name: <skill_name>
description: One line — what this skill does and when to invoke it.
model: haiku | sonnet | opus
---

<prompt body>

## Inputs
- `<context_field>`: description

## Output contract
- What the invoker expects back (format, schema, constraints)
```

## Model pins (per plan)

| Skill | Model | Rationale |
|---|---|---|
| `classify_book` | Haiku | Narrow template-selection choice |
| `summarize_capture` | Haiku | Short summarization of long captures |
| `judge_serendipity` | Haiku | Rejects shallow thematic matches; high volume |
| `generate_book_note` | Sonnet | Template-driven synthesis of real content |
| `reconcile_book` | Sonnet | Merge/dedup reasoning |
| `regenerate_profile` | Opus | Monthly; reasons across corpus + inbox + directives |

Default: **Sonnet** for new skills. Drop to Haiku if the skill is narrow/mechanical; use Opus only when reasoning is genuinely heavy.

## Authoring a new skill

```bash
make new-skill name=my_skill
```

This scaffolds `skills/my_skill/SKILL.md` from the template. Edit the body, commit.

## Hot reload

Skills are picked up from disk on each worker invocation; no restart needed. The `reload_prompts()` MCP tool can also clear any in-process caching.

## Versioning

Skills are version-controlled in this repo. A skill change that meaningfully alters output should bump an ADR if it constitutes a design decision. Routine prompt tuning doesn't need an ADR.
