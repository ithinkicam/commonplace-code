# classify_book

Classifies a book into a knowledge tier and a note template for the Commonplace system.

## Purpose

Before generating a book note, Commonplace needs two things: how much the model already knows about this book (tier), and what structural template to use for the note (template). This skill decides both from available metadata.

## Input

JSON with these fields (all strings; `subjects` is a list):

| Field | Required | Notes |
|---|---|---|
| `title` | yes | Book title |
| `author` | yes | Author name |
| `subjects` | no | List of subject/genre strings from OpenLibrary or StoryGraph |
| `description` | no | Publisher blurb or back-cover copy |
| `sample_text` | no | Brief excerpt from the book |

Callers shape this from OpenLibrary metadata and/or Kindle library data.

## Output

Single line of JSON, no surrounding prose:

```json
{"tier": "HIGH|MEDIUM|LOW", "template": "argument|narrative|poetry", "reasoning": "<≤40 words>"}
```

| Field | Values | Meaning |
|---|---|---|
| `tier` | HIGH, MEDIUM, LOW | Model's knowledge coverage; governs whether a full note is generated |
| `template` | argument, narrative, poetry | Structural template for note generation |
| `reasoning` | ≤40 words | Short explanation of the classification |

## Tiers explained

- **HIGH** — canonical works the model knows well (Kant, Austen, Keats, major popular non-fiction). Notes rely on training data at query time; structural content is skipped.
- **MEDIUM** — partial coverage; worth a dedicated note.
- **LOW** — obscure, self-published, very recent, or specialized. Commonplace's real value: notes generated from actual text.

See `commonplace-plan-v5.md` §"Generation strategy by Claude's knowledge level" for the full rationale.

## Templates explained

- **argument** — non-fiction building a thesis; default on edges
- **narrative** — fiction, memoir, history, reportage
- **poetry** — verse, lyric essay, aphorism collections

## Invocation

```bash
echo '{"title": "Critique of Pure Reason", "author": "Immanuel Kant"}' \
  | claude -p classify_book --model haiku
```

Or from a file:

```bash
cat skills/classify_book/fixtures/kant_critique.json \
  | claude -p classify_book --model haiku
```

## Updating the decision guidance

The decision heuristics live in `SKILL.md` under `## Decision guidance`. Edit the examples and threshold language there. No restart needed — skills are picked up from disk on each invocation. After any meaningful change, run:

```bash
bash scripts/smoke_classify_book.sh
```

to verify all fixtures still pass. If a fixture misfires, iterate the prompt rather than the fixture.

## Related

- Plan v5 classification section: `commonplace-plan-v5.md` §"Book classification" and §"Generation strategy by Claude's knowledge level"
- Smoke test: `scripts/smoke_classify_book.sh`
- Pytest coverage: `tests/test_classify_book_skill.py`
- Note generation templates (task 2.7, not yet built): `skills/generate_book_note/`
