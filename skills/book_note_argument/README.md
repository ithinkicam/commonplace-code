# book_note_argument

Generates a structured argument note for non-fiction books that build a thesis — philosophy, social science, business, science writing, essays.

## Purpose

Produces a Markdown note with fixed sections: thesis, core argument, key moves, objections and limits, and durable takeaways. Optionally includes verbatim quotes. Depth scales with the `tier` field (HIGH = ~400 words, MEDIUM = ~700, LOW = ~1200).

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

The note begins with `# <Title> — argument note` and always includes these headers (in order):

- `## Thesis` — one sentence
- `## Core argument` — 3–6 sentences tracing the logical arc
- `## Key moves` — 3–7 bullets on rhetorical/evidential tactics
- `## Objections and limits` — 2–5 sentences on gaps and weak points
- `## Durable takeaways` — 3–5 bullets on what survives a year later

Optional (if material supports):

- `## Quotes` — up to 5 verbatim quotes from the text

## Example invocation

```bash
cat skills/book_note_argument/fixtures/marcus_aurelius.json \
  | claude -p --system-prompt-file skills/book_note_argument/SKILL.md --model sonnet
```

## Plan v5 reference

Task 2.7 (three book note skills). Template type: **argument** — for non-fiction that builds a thesis or makes a sustained case. Default template when classification is unclear.

See `commonplace-plan-v5.md` lines 192–198, 365, 429.
