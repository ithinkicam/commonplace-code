# summarize_capture

Haiku-pinned skill that produces a short, embed-ready summary of a long capture (article, podcast transcript, YouTube transcript). The full capture text remains canonical in Commonplace; this summary feeds the vector index so long transcripts surface in `search_commonplace`.

## When this skill is invoked

Capture handlers (tasks 3.4–3.7) call this skill after a long-form capture lands in the inbox. Gate: text is roughly 2000+ whitespace-separated tokens. Shorter captures are embedded directly; no summary needed.

## Input contract

JSON object on stdin:

| Field | Required | Notes |
|---|---|---|
| `source_kind` | yes | `article`, `podcast`, `youtube`, or `other` |
| `title` | yes | Title or best-guess label for the capture |
| `text` | yes | Full capture text |
| `url` | no | Source URL, preserved only in frontmatter if caller needs it (the skill does not require it) |
| `author` | no | Speaker(s) or byline |
| `word_count` | no | Caller may precompute |

## Output contract

YAML frontmatter + Markdown body. The full spec lives in `SKILL.md`. Short form:

```
---
summary_version: 1
source_kind: article|podcast|youtube|other
title: <title>
word_count: <integer>
---
# Summary
<2-4 sentence paragraph>

## Key points
- 5-8 bullets, one sentence each

## Quotes
> verbatim quote from text
> verbatim quote from text
```

2–4 quotes, each a verbatim substring of the input `text`. If the caller passes a capture under the 2000-word threshold, the skill short-circuits to a `too_short: true` frontmatter with no body.

## Parser

A pure-Python parser/validator lives at `skills/summarize_capture/parser.py`. It has no third-party deps and exposes:

- `parse(output: str) -> CaptureSummary` — parses the skill's stdout and raises `ParseError` on any format violation.
- `verify_quotes(summary, source_text) -> list[str]` — returns any quotes that are not verbatim substrings of the input (fabrication check).
- `should_summarize(text, threshold=2000) -> bool` — gate helper for capture handlers.

## Model pin

`haiku`. See `AGENTS.md` model-pins table and `build/pins/claude-code.md` for the invocation shape.

## Invocation

```bash
cat skills/summarize_capture/fixtures/article_city_buses.json \
  | claude -p --system-prompt-file skills/summarize_capture/SKILL.md --model haiku
```

## Smoke test

```bash
bash scripts/smoke_summarize_capture.sh
```

Runs every fixture in `skills/summarize_capture/fixtures/` through the skill, parses the output with `parser.py`, and confirms every quote is a verbatim substring of the fixture `text`. Passes only if all fixtures parse and no quotes leak.

## Pytest coverage

```bash
.venv/bin/python -m pytest tests/test_summarize_capture_skill.py -v
```

Offline tests: round-trip parse, rejection of malformed outputs, length-gate behavior, fabricated-quote detection, fixture integrity.

## Preamble guard

Haiku has been observed to prefix responses with a conversational preamble ("Here is your summary:"). The output spec requires the very first character of the response to be `-` (the opening `---` of the frontmatter). `parser.py` enforces this strictly.
