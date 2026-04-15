# book_note_narrative

Generates a structured narrative note for fiction, memoir, history, biography, journalism, and reportage — books where story or lived experience is the through-line.

## Purpose

Produces a Markdown note with fixed sections: arc, voice and texture, characters or figures, images and scenes, what it turns on, and durable takeaways. Depth scales with the `tier` field (HIGH = ~400 words, MEDIUM = ~700, LOW = ~1200).

## Input

JSON on stdin:

| Field | Required | Description |
|---|---|---|
| `title` | yes | Book title |
| `author` | yes | Author name |
| `text` | yes | Full book text or concatenated chunks |
| `tier` | yes | `HIGH`, `MEDIUM`, or `LOW` — controls note depth |
| `reader_context` | no | Freeform notes on what the reader cares about |

## Output sections

The note begins with `# <Title> — narrative note` and always includes these headers (in order):

- `## Arc` — 3–6 sentences on what happens or is traced
- `## Voice and texture` — 2–4 sentences on prose style and feel
- `## Characters or figures` — 2–5 bullets on who matters and why
- `## Images and scenes` — 3–6 bullets on what lingers
- `## What it turns on` — 2–4 sentences on central tension
- `## Durable takeaways` — 3–5 bullets on what survives a year later

Optional (if material supports):

- `## Quotes` — up to 5 verbatim quotes from the text

## Example invocation

```bash
cat skills/book_note_narrative/fixtures/austen_pride.json \
  | claude -p --system-prompt-file skills/book_note_narrative/SKILL.md --model sonnet
```

## Plan v5 reference

Task 2.7 (three book note skills). Template type: **narrative** — for fiction, memoir, history, reportage. The through-line is story or lived experience, not a thesis.

See `commonplace-plan-v5.md` lines 192–198, 365, 429.
