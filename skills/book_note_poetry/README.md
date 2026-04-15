# book_note_poetry

Generates a structured note for verse, poetry collections, lyric essays, and aphorism books — works where form is primary.

## Purpose

Produces a Markdown note with fixed sections: project, form and prosody, recurring images, poems to return to, quiet center, and durable takeaways. Depth scales with `tier`, but runs shorter than argument/narrative notes because compression is intrinsic to the form (HIGH = ~250 words, MEDIUM = ~500, LOW = ~900).

## Input

JSON on stdin:

| Field | Required | Description |
|---|---|---|
| `title` | yes | Book title |
| `author` | yes | Author name |
| `text` | yes | Full book text or a representative selection of poems/sections |
| `tier` | yes | `HIGH`, `MEDIUM`, or `LOW` — controls note depth |
| `reader_context` | no | Freeform notes on what the reader cares about |

## Output sections

The note begins with `# <Title> — poetry note` and always includes these headers (in order):

- `## Project` — 2–4 sentences on what the collection is doing
- `## Form and prosody` — 2–4 sentences on formal choices and their effect
- `## Recurring images` — 3–6 bullets on images that accrue meaning
- `## Poems to return to` — up to 6 bullets with one-line notes (omitted if no named poems in text)
- `## Quiet center` — 2–4 sentences on the emotional/philosophical pull
- `## Durable takeaways` — 3–5 bullets on what survives a year later

Optional (if material supports):

- `## Lines` — up to 8 verbatim fragments from the text

## Example invocation

```bash
cat skills/book_note_poetry/fixtures/dickinson.json \
  | claude -p --system-prompt-file skills/book_note_poetry/SKILL.md --model sonnet
```

## Plan v5 reference

Task 2.7 (three book note skills). Template type: **poetry** — for verse, lyric essay, aphorism. Used when form is clearly primary.

See `commonplace-plan-v5.md` lines 192–198, 365, 429.
