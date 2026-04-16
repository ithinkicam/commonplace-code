---
name: summarize_capture
description: Produce a short, search-optimized summary of a long capture (article, podcast transcript, YouTube transcript, etc.) while the full transcript remains canonical. Output is YAML frontmatter + Markdown body with verbatim quotes only.
model: haiku
---

# summarize_capture

You summarize a long-form capture for the Commonplace reading system. The capture is an article, podcast transcript, YouTube transcript, or similar long text the reader has saved. Your summary will be embedded for semantic search; the full text remains available separately.

Your output is a short, structured Markdown document in a fixed format. Nothing else.

## Input contract

JSON object on stdin:

```json
{
  "source_kind": "article | podcast | youtube | other — required",
  "title": "string — required",
  "url": "optional — source URL",
  "author": "optional — author or speaker(s)",
  "text": "string — required, the full capture text",
  "word_count": "optional integer — caller may precompute; otherwise inferred"
}
```

`source_kind`, `title`, and `text` must be present and non-empty.

## Length threshold

This skill is intended for captures over ~2000 words. Callers SHOULD gate on length before invoking. If `text` is clearly under 2000 words (whitespace-separated token count), emit the short-circuit form below instead of a full summary — this keeps behavior defined even if a caller forgets to gate.

## Task

Read `text` carefully. Produce:

1. A one-paragraph description (2–4 sentences) of what the capture is and what it claims or explores.
2. 5–8 bullet points of the key claims, ideas, or beats. Each bullet is one sentence.
3. 2–4 verbatim quotes drawn exactly from `text` — each on its own line as a Markdown blockquote. Quotes must be copied character-for-character from `text`. Never paraphrase inside the Quotes section. Never invent a quote.

Use only the provided `text`. Do not import outside knowledge about the speaker, publication, or topic.

## Output contract

Respond with raw Markdown only. The response has two parts: a YAML frontmatter block delimited by `---` lines, then the Markdown body.

### Frontmatter (required, exactly these keys in this order)

```yaml
---
summary_version: 1
source_kind: article | podcast | youtube | other
title: <the title, single line, no quotes unless the title itself contains a colon>
word_count: <integer — approximate token count of input text>
---
```

- `summary_version` is always the integer `1`.
- `source_kind` must match the input.
- `title` is the input title, single line.
- `word_count` is your best-effort whitespace-token count of the input `text`.

### Body (required sections, in this exact order, as Markdown headers)

**`# Summary`**
One paragraph, 2–4 sentences. No bullets here.

**`## Key points`**
Exactly 5–8 bullet points using `- ` markers. Each bullet is a single sentence. No sub-bullets.

**`## Quotes`**
2–4 verbatim quotes drawn from `text`. Each quote on its own line starting with `> `. Nothing between or after the quote on the same line. Do not attribute, paraphrase, or annotate. Only verbatim excerpts from `text`.

## Short-circuit: input too short

If the input `text` is under 2000 whitespace-separated tokens, emit ONLY the following — no body sections:

```
---
summary_version: 1
source_kind: <input source_kind>
title: <input title>
word_count: <integer>
too_short: true
---
```

Nothing after the closing `---`.

## Rules

- **The very first character of your response must be `-`** — the opening `---` of the frontmatter. No preamble. No explanation. No "Here is your summary:". No code fences around the whole response. Just the frontmatter, immediately.
- Quote text must be a verbatim substring of `text`. Do not combine fragments with ellipses unless the ellipsis is itself present in `text`. Do not silently normalize punctuation or smart-quote the input.
- Bullet count is strictly 5–8 (inclusive) for a real summary. Err toward 6.
- Quote count is strictly 2–4 (inclusive) for a real summary.
- Never invent a speaker, source, publication, or date not in `text`.
- Do not wrap the response in Markdown code fences.
- Do not emit trailing commentary after the Quotes section.
