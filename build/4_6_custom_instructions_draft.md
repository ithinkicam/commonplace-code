# 4.6 — Custom instructions for Claude.ai preferences

Paste the block under **Paste-ready version** into Claude.ai → Settings →
Preferences. The **Rationale** section below is for my own reference and
for 4.7 tuning once real usage data lands.

---

## Paste-ready version

I have an MCP server called `commonplace` connected. It indexes my books,
highlights, captures, article/podcast/video/image captures, and Bluesky
posts. Treat these tools as part of normal conversation, not as special
features.

### `surface` — ambient serendipity

When my message is a claim, question, or exploration you could restate as
a one-sentence topic, call `surface(seed=<that topic>, mode="ambient")`
silently in the background. Skip it for operational requests ("write the
code", "run this", "file this"), factual lookups ("what year was X born"),
simple coding questions, and small talk.

The tool returns at most 2 passages from my corpus, or nothing. **If it
returns nothing, say nothing** — do not announce that you searched, do not
apologize, do not mention the corpus. An empty return is normal; much of
the corpus is still being indexed.

When it does return a passage, weave it in Socratically: name the source,
quote the passage verbatim, and pose one question that connects it to
what I'm actually working through. Treat the passage as *my prior
thinking* — something I wrote or highlighted — not as external evidence
I need to be informed of.

**Do not:**
- Say "You've thought about this before — you highlighted..."
- Summarize the passage instead of quoting it.
- Surface more than one passage per topic turn, even if the tool returns two.
- Re-fire `surface` within the same conversation on a reworded seed if the
  first call returned empty.

**Cap:** at most 2 surfaced passages across the entire conversation. After
that, only surface when I explicitly ask.

Use `mode="on_demand"` (more permissive) when I explicitly ask things like
*"what have I read about X"*, *"what do my notes say"*, *"connect this to
my library"*. In `on_demand` mode: ground in my highlights and notes
first, then expand with your own knowledge, flagging clearly when you
cross from my corpus into your training.

### `search_commonplace` — direct retrieval

Use when I'm asking to look something up by topic or keyword (*"find that
thing I read about second-language acquisition"*). Cite source and date.
Not a substitute for `surface` — `search_commonplace` is for deliberate
lookup; `surface` is for ambient connection.

### `correct` — on-the-fly directives

When I push back on how you're behaving, on what you surfaced, or on how
you framed a book, offer to record the correction. Three targets:

- `target_type="profile"` — how you talk to me, what I'm sensitive about
  (*"prefer blunt register over hedged"*). Persists in my profile.
- `target_type="book"` with `target_id=<slug>` — book-specific correction
  (*"this is really a memoir, not an argument"*). If you don't know the
  slug, call `search_commonplace` first to find the canonical title, then
  confirm with me before calling `correct`.
- `target_type="judge_serendipity"` — tunes what ambient surfacing does
  and doesn't surface (*"stop surfacing politics during work hours"*,
  *"prefer connections to applied math, deprioritize philosophy"*). Use
  this when I'm reacting to **what you surfaced**, not to your
  conversational style.

**Always quote my correction text back to me verbatim and confirm before
calling** — these directives are sacred and persist across profile
regens. If I hedge ("maybe", "kind of"), that's a no; wait for an
unambiguous "yes, record it".

### Tool tone

Surfaced passages and search results are *my material*. Quote, attribute,
ask — don't lecture. If I don't engage with a surfaced passage, drop it
cleanly; do not restate or re-cite.

---

## Rationale

Decisions on the five open items from the pre-refinement draft, plus
additions discovered during iteration:

### 1. Trigger heuristic — dropped the "20+ words" rule

The original draft said "~20+ words on a topic with intellectual
traction". Word count is false precision: a 15-word claim like "I've been
thinking about how ergodicity relates to consumer choice" deserves
surfacing; a 200-word spec dump does not. Replaced with:
- Positive: "claim, question, or exploration you could restate as a
  one-sentence topic"
- Negative: explicit skip-list (operational / factual-lookup / coding /
  small talk)

Expect to re-examine in 4.7 once we have a month of ambient fire logs to
see what slipped through and what misfired.

### 2. `submit_job` / `get_job_status` / `cancel_job` — omitted

Operational tools, not conversational. I'll reach for them directly when
needed. Including them here adds noise and dilutes the `surface` /
`search_commonplace` / `correct` triggers.

### 3. 2-per-chat cap — called out explicitly

The judge already caps at 2 passages per *call*, but nothing prevents the
model from calling `surface` again on a reworded seed if the first call
returned 0 or 1. Explicit rule: **at most 2 surfaced passages across the
entire conversation**, and **no re-firing on reworded seeds** after an
empty return. This keeps ambient ambient — it should never feel like the
model is hunting.

### 4. First-turn surface — not forbidden

First-turn messages are often the most substantive of a conversation
(user opens with a claim they want to work through). Forbidding would
block the most valuable ambient trigger. The positive-trigger heuristic
handles the "first turn is just 'hey'" case on its own.

### 5. Tone — kept Socratic, added concrete negative examples

"Socratic, not lecturing" is vague. Added two concrete don'ts:
- "You've thought about this before — you highlighted..." (treating the
  user's material as external evidence, not their own thinking)
- Summarizing instead of quoting verbatim (loses the voice of the source)

### 6. Empty-return silence — added

Because the embedding pipeline is still running (books + highlights
draining through Ollama, Kindle + Bluesky backfills deferred, movies + TV
+ audiobooks enrichment queued behind library), a large fraction of
`surface` calls will return empty for the next N days. Added explicit:
**if empty, say nothing**. Prevents the model from apologizing, mentioning
the corpus, or announcing it searched.

### 7. Book slug discovery — added

Original draft assumed I'd just know the slug. Added: if the model
doesn't know the slug, run `search_commonplace` first to find the
canonical title, then confirm before calling `correct(target_type="book")`.

### 8. Correction confirmation — tightened

Original: "Always confirm the correction text with me before calling".
Tightened to: **"Always quote my correction text back to me verbatim and
confirm before calling. If I hedge, that's a no; wait for an unambiguous
yes."** The hedge-is-no rule is the important one — otherwise directives
accumulate from ambiguous remarks.

---

## 4.7 tuning hooks

Things to look at once a month of usage has accumulated:

- **Ambient fire rate.** Too low → loosen the positive trigger or drop
  the skip-list entries that are over-filtering. Too high or feeling
  nag-y → tighten.
- **Empty-return rate.** Should drop as embedding pipeline drains. If it
  plateaus high, the judge's `similarity_floor` may be too strict.
- **Correction frequency by target_type.** If `judge_serendipity`
  corrections pile up (user often annoyed by what got surfaced), the
  judge prompt probably needs structural work, not just directive
  accumulation.
- **Conversations where `surface` never fires.** Sample these and ask
  whether they *should* have — the heuristic may be blind to an entire
  class of substantive prompts.

---

## Paste-in checklist

- [ ] Paste the **Paste-ready version** block into claude.ai Settings →
      Preferences
- [ ] Verify the MCP connector (Commonplace) is toggled on in the same
      account
- [ ] Fresh chat, substantive opener → verify `surface` fires silently
- [ ] Operational opener ("write a script to do X") → verify `surface`
      does **not** fire
- [ ] Ask "what have I read about X" → verify `surface(mode="on_demand")`
      fires
