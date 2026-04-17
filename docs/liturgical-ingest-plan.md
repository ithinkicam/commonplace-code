# Liturgical Ingest — Technical Plan

**Status:** Draft 1 — awaiting review. Composed 2026-04-17.
**Source:** User's design doc "Commonplace: Liturgical Ingest — Design & Build Plan" (kept in conversation; not yet checked in).
**Purpose:** Convert that design into a build-ready plan grounded in Commonplace's actual codebase, with risk register and feasibility findings.

---

## 0. How to use this doc

This plan assumes the design doc's framing (problem statement, design principles, success criteria) as given. It does not restate them. What it adds:

- Findings from a codebase pass — schema shape, ingestion pattern, retrieval stack, judge rubric — so the plan fits what exists rather than what the design doc assumed.
- Findings from a feasibility pass on each source (BCP 1979, LFF 2022, Jordanville) — actual HTML/PDF samples, library choices, structural quirks.
- A revised architecture, risk register, phased plan, and answers to the eight Gaps the design doc left for Claude Code to fill.

Iterate by editing sections in place. Section §6 (open questions) is where decisions live — resolve those before Phase 0 starts.

---

## 1. Corrections to the design doc

Research surfaced six things that amend the design doc:

1. **BCP 1979 source is bcponline.org, not justus.anglican.org.** Justus (Wohlers collection) only publishes 1979 as RTF/DOC/PDF; its HTML pages just link out. bcponline.org is the canonical HTML edition.
2. **LFF 2022 was authorized by Resolution A007, not A059.** Minor but worth fixing in any future write-up.
3. **There is no `content_type` enum to extend.** Commonplace uses a free-text `TEXT` column on `documents` (current values: `book`, `audiobook`, `article`, `podcast`, `youtube`, `bluesky_post`, `kindle_book`, `kindle_highlight`, etc., defined implicitly by handler registration in `commonplace_worker/worker.py`). Adding `liturgical_unit` is a string literal change, not a migration.
4. **Chunker will merge short units.** `commonplace_server/chunking.py` has a 400-token merge floor — any troparion, collect, or kontakion (typically 50–200 tokens) will be combined with adjacent content if ingested inside a larger document. The pilot has to ingest liturgical content one-document-per-unit (see §2.2).
5. **The design-doc schema sketch (three flat tables: `liturgical_unit`, `feast`, `commemoration_bio`) assumes a richer data model than Commonplace has.** Commonplace has a single `documents` table with free-text content type and a sidecar-friendly shape. Fitted schema uses sidecar tables keyed on `document_id` (see §2.1).
6. **Jordanville deferred to post-pilot.** Source is a Kindle purchase (KFX/AZW3); deDRM/conversion handled as separate user workstream. Pilot runs on BCP 1979 + LFF 2022 only — pure Anglican. Cross-tradition validation (design-doc test #3) is gated on Jordanville and also deferred. See §6 Q1 resolution.

---

## 2. Architecture, fitted to Commonplace as it exists

### 2.1 Schema — sidecar tables over shared `documents`

Current shape (relevant tables only):

| Table | Key fields | Purpose |
|---|---|---|
| `documents` | `id`, `content_type`, `source_uri`, `source_id`, `title`, `author`, `content_hash`, `status`, `raw_path` | Every ingested artifact |
| `chunks` | `id`, `document_id` (FK), `chunk_index`, `text`, `token_count` | Text split for embedding |
| `embeddings` | `id`, `chunk_id` (FK UNIQUE), `model`, `vector_blob` | Canonical vector store |
| `chunk_vectors` | `chunk_id`, `embedding` | sqlite-vec virtual table (ANN index) |
| `job_queue` | `id`, `kind`, `payload`, `status`, `attempts`, `error` | Async ingest queue |

Proposed additions in a single migration (next available number, likely `0007_liturgical_ingest.sql`):

```
CREATE TABLE liturgical_unit_meta (
  document_id          INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  category             TEXT NOT NULL,    -- liturgical_proper | devotional_manual | psalter | hagiography
  genre                TEXT NOT NULL,    -- collect | troparion | kontakion | canticle | prayer | ...
  tradition            TEXT NOT NULL,    -- anglican | byzantine | roman | shared
  source               TEXT NOT NULL,    -- bcp_1979 | lff_2022 | jordanville
  language_register    TEXT,             -- rite_i | rite_ii | traditional | modern | NULL
  office               TEXT,             -- morning_prayer | evening_prayer | eucharist | compline | hours | other | NULL
  office_position      TEXT,             -- opening | general | after_communion | dismissal | NULL (source-specific allowed)
  calendar_anchor_id   INTEGER REFERENCES feast(id),
  canonical_id         TEXT,             -- shared across Rite I/II duplicates and cross-source copies
  raw_metadata         TEXT              -- JSON: tone, mode, page ref, pdf coords, etc.
);

CREATE INDEX idx_liturgical_meta_category ON liturgical_unit_meta(category);
CREATE INDEX idx_liturgical_meta_genre ON liturgical_unit_meta(genre);
CREATE INDEX idx_liturgical_meta_tradition ON liturgical_unit_meta(tradition);
CREATE INDEX idx_liturgical_meta_feast ON liturgical_unit_meta(calendar_anchor_id);
CREATE INDEX idx_liturgical_meta_canonical ON liturgical_unit_meta(canonical_id);

CREATE TABLE feast (
  id                             INTEGER PRIMARY KEY,
  primary_name                   TEXT NOT NULL,
  alternate_names                TEXT,             -- JSON array
  tradition                      TEXT NOT NULL,    -- anglican | byzantine | shared
  calendar_type                  TEXT NOT NULL,    -- fixed | movable | commemoration
  date_rule                      TEXT NOT NULL,    -- 'MM-DD' | 'easter+0' | 'easter-46' | etc.
  precedence                     TEXT NOT NULL,    -- principal_feast | holy_day | lesser_commemoration | ferial
  theological_subjects           TEXT,             -- JSON array
  cross_tradition_equivalent_id  INTEGER REFERENCES feast(id),
  created_at                     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at                     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_feast_tradition ON feast(tradition);
CREATE INDEX idx_feast_date_rule ON feast(date_rule);

CREATE TABLE commemoration_bio (
  id          INTEGER PRIMARY KEY,
  feast_id    INTEGER NOT NULL REFERENCES feast(id),
  document_id INTEGER REFERENCES documents(id),  -- if the bio is also embedded as prose
  text        TEXT NOT NULL,
  source      TEXT NOT NULL
);

CREATE INDEX idx_bio_feast ON commemoration_bio(feast_id);
```

Notes:
- `title` and `author` stay on `documents` (already there). Don't duplicate.
- `raw_metadata` as TEXT holds JSON, matching the existing convention of `job_queue.payload` being a JSON string (Commonplace uses SQLite; no JSONB).
- `canonical_id` is a free-text slug (e.g., `collect_for_purity`) that groups Rite I/II variants and cross-source duplicates. See §5, Gap 7.
- `cross_tradition_equivalent_id` is self-referential. Single-direction is enough for pilot; if you later want bidirectional lookup, add a view.
- `commemoration_bio.document_id` lets a biographical note behave like prose at retrieval time (per the design doc's §Architecture note) — it's embedded as a regular document, and the bio row links it to the feast.

### 2.2 Ingestion shape — one-document-per-unit

Commonplace's chunker (`commonplace_server/chunking.py`, ADR-0005) merges paragraphs below a 400-token floor. A 60-token troparion ingested inside a book-level document will be glued to whatever paragraph follows it, destroying the "one unit, one surface" invariant that the whole feature rests on.

Three options:

| Option | Description | Verdict |
|---|---|---|
| A | One book-level `documents` row, one `chunks` row per liturgical unit | Fails: chunker merges across unit boundaries |
| B | One `documents` row per liturgical unit | **Recommended** — preserves granularity, simplifies metadata linkage, retrieval returns the unit directly |
| C | Bypass chunker for liturgical content (custom insert path) | Works but breaks the invariant that every document flows through `chunk_text()` |

**Option B costs: row count.** Rough estimate for pilot:

| Source | Units |
|---|---|
| BCP 1979 Collects | ~170 (Rite I + Rite II + seasonal + propers) |
| BCP 1979 Daily Office | ~50 distinct prayers/canticles |
| BCP 1979 Psalter | 150 |
| BCP 1979 Proper Liturgies | ~30 prayers + speaker dialogue blocks |
| BCP 1979 Prayers & Thanksgivings | 70 |
| BCP 1979 Other (Baptism/Eucharist/Pastoral/Episcopal/Catechism) | ~100 |
| LFF 2022 | ~400 (≈200 commemorations × 2 rites) |
| LFF 2022 bios | ~200 |
| Jordanville | ~200 |

Total: ~1,400 new `documents` rows + matching `chunks` + `embeddings` + `liturgical_unit_meta` rows. Well within what SQLite + sqlite-vec handle; ADR-0005 assumes "single-digit thousands to low tens of thousands" as the operating envelope.

### 2.3 Handler

One new file: `commonplace_worker/handlers/liturgy.py`, with three job kinds for clarity (different failure modes, different parsers):

- `ingest_liturgy_bcp`
- `ingest_liturgy_lff`
- `ingest_liturgy_jordanville`

Each follows the existing handler signature:

```python
def handle_liturgy_bcp_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_liturgy_bcp' jobs.

    payload: {html_cache_dir: str, force_reembed?: bool}
    Returns: {units_new, units_skipped, units_failed, elapsed_ms}
    """
```

Registered in `commonplace_worker/worker.py:HANDLERS`.

Per-unit insertion flow:

1. Parse source → yield structured unit records (text, metadata).
2. For each unit:
   a. Compute `content_hash` on the unit text.
   b. `INSERT OR IGNORE` into `documents` with `content_type='liturgical_unit'`, `source_uri='bcp1979://collects/first-sunday-advent#rite-ii'` (or equivalent), `source_id=<canonical_id-plus-rite>`.
   c. `INSERT` into `liturgical_unit_meta`.
   d. Call `embed_document(document_id, conn)` (existing function in `commonplace_server/pipeline.py`) — short units produce one chunk.
3. Return summary dict.

Idempotency: `(content_type, source_id)` UNIQUE index already exists (migration 0003); re-ingest is a no-op. `content_hash` is the fallback check.

### 2.4 Parsers

#### BCP 1979 — bcponline.org

- **Library:** BeautifulSoup + lxml backend. (lxml's recovery mode handles the malformed Psalter markup that `html.parser` chokes on; selectolax struggles with duplicated tags; trafilatura is article-extractor and flattens the structural cues we need.)
- **Robots:** bcponline.org sets `Crawl-delay: 180`. A polite single-pass crawl of ~150–250 pages takes 8–13 hours. **Build a local HTML cache as the first step of the parser** — one overnight crawl, then all parser iteration runs off cache.
- **Structure:** Presentational HTML with a small, stable CSS class vocabulary:
  - `class="rubric"` — instructions (italic)
  - `class="small"` / `class="x-small"` — citations
  - `class="leftfoot"` / `class="rightfoot"` — printed-page markers (every BCP page boundary emits one, followed by `<hr>`) — gold for traceability
  - Psalter: `psnum`, `pshead`, `pslatin`, `vsnum`, `psday`
  - Speaker dialogue: borderless `<table>` with `class="rubric"` on first column
- **Rite discriminator:** filename-based (`mp1.html` / `mp2.html`, `euchr1.html` / `euchr2.html`, `toctradit.html` / `toccontemp.html`). Trivial.
- **Strategy:** iterate top-level block elements in document order, emit a typed event stream (`page_break`, `heading`, `rubric`, `prayer_body`, `citation`, `speaker_line`, `psalm_verse`, `amen_end`). Group by nearest preceding `<strong>` / `<h*>` / `id=` to get occasion name.
- **Risk per section** (no-manual-tagging constraint):
  - Collects — **LOW**. Rigidly regular.
  - Daily Office — **LOW-MEDIUM**. Seasonal sentences use bare `<em>Advent</em>` markers rather than class; one regex rule.
  - Psalter — **MEDIUM**. Malformed source HTML (lxml recovery handles); anchor `id` collisions; Latin incipits in `pslatin`.
  - Proper Liturgies — **MEDIUM-HIGH**. Heterogeneous: inline-styled optional blocks (Ash Wednesday), speaker tables (Palm Sunday), embedded psalms.
  - Prayers & Thanksgivings — **LOW**. Numbered 1–70, anchor-addressable, consistent shape.
- **Effort:** ~400–600 LOC for the parser (one selector map per section + shared rubric/speaker/verse helpers). Budget 1–2 days of quirk handling on top of parser body.

#### LFF 2022 — PDF

- **Library:** PyMuPDF (`fitz`). Its `page.get_text("dict")` exposes font name + size + style flags per text span, which is exactly the signal LFF uses to mark structure.
- **Source:** `https://www.episcopalcommonprayer.org/uploads/1/2/9/8/129843103/lesser_feasts_and_fasts_2022_final.pdf` (638 pages, 4.9 MB, PDF 1.7, native digital typography). **No OCR needed.**
- **Copyright:** Church Publishing. Personal/parish reproduction permitted; redistribution requires permission. See §3 R3.
- **Structure signals (verified on a real entry):**

| Signal | Semantic |
|---|---|
| `Sabon-Bold` 17pt | Commemoration name (entry boundary) |
| `Sabon-Italic` 9pt, before name | Date header ("January 9") |
| `Sabon-Italic` 9pt, after name | Rank/title ("Lay Leader and Missionary, 1922") |
| Paragraph starting with `"I"` (Sabon-Roman 11) + tab | Rite I collect start |
| Paragraph starting with `"II"` + tab | Rite II collect start |
| `Sabon-Italic "Amen."` | Collect terminator |
| `Sabon-Bold` 11pt `"Lessons and Psalm"` | Readings marker |
| `Sabon-Italic "Preface of …"` | Entry footer |
| `Sabon-Roman` 9pt body | Biographical note |

- **Strategy:** one pass over spans; emit new entry on each size-17 Bold fire; state machine keyed on `(font, size, leading token)` within an entry.
- **Scripture references:** stored as text citations on the commemoration (in `liturgical_unit_meta.raw_metadata` or as separate fields) — not ingested as liturgical_unit rows. If the corresponding psalms are in the corpus (via BCP Psalter ingest), the resolver can point to them at query time.
- **Effort:** ~200–300 LOC. Budget 1–2 days including validation pass against `lectionarypage.net` for scripture refs.

#### Jordanville — DEFERRED POST-PILOT

Gated on user's Kindle deDRM workstream delivering a usable text file. When it returns to scope:

- **Library (assuming epub post-conversion):** `ebooklib` (aerkalov/ebooklib, v0.20 2025-10-26, actively maintained) + lxml for body XHTML.
- **Conversion chain:** Kindle KFX/AZW3 → epub via Calibre `ebook-convert`, or direct if deDRM workstream produces epub.
- **Title/author regex:** needs multiple extractors (one per genre — Prayer of X, Troparion. Tone N, Kontakion, Canon, Akathist) plus an "untitled" bucket. Design-doc assumption that a single regex captures authors will miss roughly half the corpus.
- **Effort:** 3–5 days when it returns + variance for conversion-chain work.

### 2.5 Calendar resolver

Module: `commonplace_server/liturgical_calendar.py`.

- **Movable feasts:** `dateutil.easter` (with `EASTER_WESTERN` / `EASTER_ORTHODOX` switch) + `timedelta` arithmetic. Helper: `movable_feasts_for_year(year, tradition) -> dict[str, date]` returning Septuagesima (Easter − 63d), Ash Wednesday (− 46d), Palm Sunday (− 7d), Easter, Ascension (+ 39d), Pentecost (+ 49d), Trinity Sunday (+ 56d), etc.
- **Fixed feasts:** lookup in the `feast` table by `date_rule` column (e.g., `'08-15'` for Dormition/Saint Mary the Virgin).
- **Anglican precedence:** write from scratch (~300–500 LOC). No existing Python library implements LFF 2022 rules; closest reference is `blocher/dailyoffice2019`'s `churchcal` app, but it's BCP 2019 (ACNA), Django-coupled, and not directly liftable. Extract as reference, not as dependency.

  Algorithm: given `(date, tradition='anglican')`:
  1. Enumerate candidate observances: fixed saints on that date, movable feasts falling on that date, the day-of-week Sunday cycle, any transferred observances from earlier days.
  2. Apply precedence ladder: Principal Feast > Sunday > Holy Day > Lesser Commemoration > Ferial.
  3. Apply Lenten/Holy Week suppression: lesser commemorations suppressed during Lent and Holy Week; transferred Holy Days move to the next open weekday (not a Sunday, not within Holy Week).
  4. Return ordered list of governing observances with transfer annotations.
- **Byzantine:** fixed-date lookup only for pilot. Full Typikon (Octoechos tone cycle, Festal-ordinary conflicts, Paschal cycle interactions) is out of scope.
- **Test fixtures:** cross-check outputs against `lectionarypage.net/CalndrsIndexes/Calendar2026.html` (and the 2025 equivalent) for every Sunday + Holy Day. Ship with known-good tables.

### 2.6 Feast table population tooling

- **File:** `commonplace_db/seed/feasts.yaml` — hand-edited, version-controlled.
- **Schema validator:** Pydantic or jsonschema; catches typos in `calendar_type`, `precedence`, bad date rules. Also validates `theological_subjects` entries against the controlled list + `_other:<freeform>` escape hatch (§6 Q5).
- **Importer:** a new `commonplace` CLI subcommand (`commonplace feast-import`) or a `make seed-feasts` target that runs an idempotent upsert — re-running after an edit updates rows, doesn't duplicate.
- **Controlled vocabulary file:** `commonplace_db/seed/theological_subjects.yaml` — list of allowed subjects with brief definitions. Edited alongside `feasts.yaml` when promoting `_other:` tags.
- **MCP audit tool — `subject_frequency`** (registered in `commonplace_server/server.py`):
  ```
  Input: { include_controlled?: bool=true, include_other?: bool=true, min_count?: int=1 }
  Output: {
    controlled: [ { subject: "theotokos", count: 12, feasts: ["Dormition", ...] }, ... ],
    other:      [ { subject: "virginity",  count: 4,  feasts: [...] }, ... ]   # sorted by count desc
  }
  ```
  Claude.ai uses this to identify promotion candidates (e.g., any `_other:` tag with count ≥ 3 across distinct feasts) and edits `theological_subjects.yaml` + `feasts.yaml` directly via file tools. No write endpoint — the editing happens through normal file edits + re-import.
- **Size:** ~200–300 feast entries. User populates in editor. Budget 2–4 hours user time for pilot. Tools can't curate `theological_subjects` — that's the one curated artifact in the whole design, though the audit tool lowers the curation cost.

Sample entry:

```yaml
- primary_name: Dormition of the Theotokos
  alternate_names: [Falling Asleep of the Virgin Mary, Koimesis]
  tradition: byzantine
  calendar_type: fixed
  date_rule: "08-15"
  precedence: principal_feast
  theological_subjects: [theotokos, death, kenosis, repose]
  cross_tradition_equivalent: saint_mary_the_virgin_anglican
```

The `cross_tradition_equivalent` field resolves to `feast.id` at import time (stored as `cross_tradition_equivalent_id`).

### 2.7 Embedding strategy for liturgical units

**The problem** (design-doc Gap 5): nomic-embed-text is a general-purpose model. A bare 60-token troparion — "In giving birth you preserved your virginity…" — carries weak discriminative signal. It lacks context the embedding can latch onto (whose, when, which feast).

**Recommendation: prepend structural context** for short units (<300 tokens), pass-through for longer ones. The embedded string for a troparion becomes:

```
Troparion for the Dormition of the Theotokos (Byzantine, Tone 1).
In giving birth you preserved your virginity...
```

This puts the unit in the semantically-correct neighborhood before the model sees the body text.

**Trade-off:** embedded text diverges from display text. Two options:

- (X) Store **composed string** in `chunks.text`, strip at retrieval time. Clean for vector store; messy for display.
- (Y) Store **display text** in `chunks.text`, compose a **different string** for the embedder. Clean for display; requires a pipeline change.

**Recommend (Y).** It keeps `chunks.text` authoritative for display and preserves the invariant that what users see is what was written. Implementation: `embed_document` in `commonplace_server/pipeline.py` gains an optional `embed_text_override: Callable[[Chunk], str] | None` parameter. The liturgical handler passes a callable that composes "{genre} for {feast} ({tradition}, {tone}). {chunk.text}".

**Consequence:** a "Collect for Purity" that already exists in the corpus as a book excerpt embeds differently than the new liturgical_unit version of the same text. They'll sit at different points in vector space. Accepted — the liturgical version *should* surface preferentially for prayer-as-prayed queries. If you ever want them to collide, re-embed the book excerpt through the same composer.

### 2.8 Judge extension

**The problem** (design-doc Gap 4): the current judge (`skills/judge_serendipity/SKILL.md`) scores on "is this genuinely relevant prior thinking," with strong rejection defaults. For liturgy the question is different — "is this the prayed response the tradition gives" — and the right framing is different (quoted with feast/office context, not presented as analytic excerpt).

**Recommended approach:** modify `SKILL.md` directly (not `directives.md`, which is user-authored and loaded runtime).

Add a new section, "Liturgical candidates," with these instructions:

- When `source_type == 'liturgical_unit'` and `category ∈ {liturgical_proper, devotional_manual}`: the acceptance test shifts. Relevance is theological-subject match (via the feast), not vocabulary overlap. A hymn for the Dormition is relevant to a conversation about Marian kenosis even if the word "kenosis" never appears in the hymn.
- For `category == hagiography`: behave like prose. (Bios are narrative; they're analyzable.)
- Do not score liturgical units for "new angle" or "counter-move" — those are prose criteria. Score for "is this the prayed response of the tradition to what's being discussed."
- Emit a `frame` field on accepted liturgical candidates: `"liturgical_ground"` for proper/devotional, absent for hagiography. The caller uses this to present with feast + office context ("The tradition prays this here") rather than as an analytic excerpt.

**Regression guard (critical):** freeze 20 surfacing examples of the current prose-only behavior as fixtures *before* editing SKILL.md. Diff judge outputs pre/post. Do not ship the rubric update if prose outputs change materially. (See §3 R5.)

**Schema thread-through:** the judge receives candidates as a JSON payload; today it gets `source_type` (e.g., `book`, `bluesky_post`). For liturgical_unit candidates, `surface.py` has to also attach `category`, `genre`, `feast_name`, `tradition` — a small change in the candidate hydration step (`surface.py:266, 288`).

---

## 3. Risk register

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Chunker merges short units (§1.4) | HIGH → LOW | One-document-per-unit (§2.2). Verified by unit test that a 60-token input survives `chunk_text()` as a single `Chunk`. |
| R2 | bcponline.org 180s crawl delay (8–13hr) | MEDIUM | Local HTML cache as first step of parser. Do the crawl overnight. Re-parse runs off cache. |
| R3 | ~~LFF 2022 redistribution rights~~ — **CLOSED** (§6 Q2: corpus private, liturgy never public) | — | — |
| R4 | ~~Jordanville source format unknown~~ — **REMOVED**, deferred post-pilot (see §6 Q1) | — | — |
| R5 | Judge regression on prose behavior | HIGH | Freeze 20 prose-surfacing fixtures as regression tests before editing SKILL.md. Don't ship if outputs diverge. |
| R6 | Feast table hand-curation (200–300 rows) | MEDIUM | Accepted per design principle. YAML-first tooling makes edits cheap; budget 2–4 hours user time for pilot. |
| R7 | Cross-tradition equivalences are judgment calls | MEDIUM | Validation test #3 is the gate. Failures mean tune `theological_subjects`, not reshape schema. |
| R8 | Calendar transfer-rule edge cases | MEDIUM | Test against `lectionarypage.net` for every Sunday + Holy Day in 2025/2026. Ship with known-good fixture table. |
| R9 | Composed-string embedding surface divergence | LOW-MEDIUM | Accept as feature, not bug. Liturgical version should sit closer to prayer-as-prayed semantics. Re-embed old book excerpts if convergence ever becomes desirable. |
| R10 | Pilot scope creep (every liturgical source tempting) | MEDIUM | §5 validation criteria are the gate. No expansion before all three pass. |
| R11 | `directives.md` shadowing | LOW | Directives (user-authored, loaded at runtime) override default rubric. Document the liturgical-rubric change prominently so users don't redefine it in their directives. |

---

## 4. Revised phased build plan

Structure follows the design doc's five phases; estimates grounded in the findings above.

### Phase 0 — Schema + feast table (3–4 days)
- Migration `0007_liturgical_ingest.sql`: add `liturgical_unit_meta`, `feast`, `commemoration_bio` tables + indexes
- YAML schema for `feasts.yaml` + Pydantic validator
- `commonplace feast-import` CLI + `make seed-feasts` target
- User populates `feasts.yaml` for pilot scope (200–300 entries, 2–4hr user time)
- Calendar resolver stub (movable-feast helper + fixed-date lookup; precedence in Phase 4)
- Unit tests on schema, resolver helpers

### Phase 1 — BCP 1979 parser (5–7 days)
- Caching crawler for bcponline.org (overnight first pass; subsequent parses from cache)
- Section parsers: collects, Daily Office, Psalter, Proper Liturgies, Prayers & Thanksgivings
- Rite discriminator via filename
- `liturgy.py` handler + `ingest_liturgy_bcp` job kind
- One-document-per-unit ingestion with canonical_id grouping (Rite I / Rite II)
- Composed-embedding-string path (§2.7, option Y) — implement `embed_text_override` in pipeline
- Integration test: job submit → worker polls → documents + meta + embeddings populated → `search_commonplace` + `surface` both return units

### Phase 2 — LFF 2022 parser (3–4 days)
- Download PDF once, pin hash
- PyMuPDF font-signal state-machine parser
- Handler + `ingest_liturgy_lff` job kind
- bios → `commemoration_bio` + `documents` (as prose); collects → `liturgical_unit` rows
- Scripture refs stored as raw_metadata strings (resolve at retrieval time if matching psalms exist in corpus)
- Integration test
- Cross-check against `lectionarypage.net` entries for 20 commemorations

### Phase 3 — DEFERRED POST-PILOT
Jordanville work is gated on user's Kindle deDRM workstream. When it returns to scope, rescope against the schema as it actually exists after pilot validation. See §6 Q1.

### Phase 4 — Retrieval integration (2–3 days)
- Judge rubric update in `SKILL.md` with "Liturgical candidates" section
- **Two paired fixture sets** (§6 Q4):
  - Prose regression: 20 pinned pre-change prose surfacings; Moderate bar (score drift OK, accept/reject flips trigger review — liturgy-spillover blocks, defensible drift ships with note)
  - Liturgical fixtures: ~10 positive cases (theological seed → liturgy should surface) + ~10 negative cases (ordinary/technical seed → no liturgy should surface)
- `search_commonplace` filter extensions: `category`, `genre`, `tradition`, `feast_name`, `date_from`/`date_to` as calendar-range (resolves via feast table)
- Calendar resolver precedence implementation (LFF 2022 rules)
- `surface.py` hydration: attach `category`, `genre`, `feast_name`, `tradition` to liturgical candidates

### Phase 5 — Validation (1–2 days)
With Jordanville deferred, pilot validation is pure-Anglican. Revised tests:

- **Test 1** (direct calendar lookup): "What's today's collect" with LFF 2022 precedence applied. Tests schema + parsers + calendar resolver.
- **Test 2** (thematic within-tradition): a conversation about mercy surfaces a mercy-themed BCP collect (e.g., Proper 21) *and* an LFF commemoration bio of a figure whose life pattern echoes the theme (e.g., a confessor or martyr whose bio touches mercy). Tests embedding + judge + feast-table subject propagation inside one tradition.
- **Test 3** — **BLOCKED on Jordanville.** Cross-tradition surfacing cannot be validated in pilot because we have no Byzantine corpus. The schema's cross-tradition machinery (`cross_tradition_equivalent_id`, shared `theological_subjects` across traditions) gets built and lies latent until Jordanville returns. First exercise of test 3 happens when Jordanville lands post-pilot.

Iterate on `theological_subjects` arrays if test 2 fails (expected failure mode; schema is fine).

**Total:** ~12–17 days of focused work with Jordanville out. Post-pilot Jordanville adds 3–5 days + conversion-chain variance.

---

## 5. Answers to the design doc's Gaps list

| Gap | Answer |
|---|---|
| 1. SQL DDL | Migration `0007_liturgical_ingest.sql` with sidecar tables (§2.1). No content_type enum change (none exists). |
| 2. Parser implementations | BCP: BeautifulSoup+lxml on cached bcponline.org HTML. LFF: PyMuPDF font-signal state machine. Jordanville: ebooklib+lxml, pending format confirmation. (§2.4) |
| 3. Calendar resolver | `dateutil.easter` + timedelta for movable; LFF 2022 precedence written from scratch (~300–500 LOC); cross-check against lectionarypage.net. (§2.5) |
| 4. Judge rubric extension | New "Liturgical candidates" section in `SKILL.md`; regression harness on 20 pinned prose examples; `frame` field in judge output. (§2.8) |
| 5. Embedding strategy | Compose structural prefix ("{genre} for {feast} ({tradition}). {text}") for short units; store display text unchanged in `chunks.text`; add `embed_text_override` seam in pipeline. (§2.7) |
| 6. Feast table tooling | `commonplace_db/seed/feasts.yaml` + Pydantic validator + idempotent importer CLI. Hand-edited. (§2.6) |
| 7. Duplicate handling | Separate units, shared `canonical_id` in `liturgical_unit_meta`. Both embed; both surface-eligible. Dedup at display time if needed. Cross-source dedup deferred post-pilot. |
| 8. Incremental reindex | Feast-table edits that change `theological_subjects` → CLI `commonplace feast-reindex <feast_id>` re-queues `embed_document` for every unit with `calendar_anchor_id = feast_id` (because the composed embedding string includes the feast name). |

---

## 6. Open questions — resolve before Phase 0

1. **Jordanville source format.** — **RESOLVED 2026-04-17:** Kindle purchase; deDRM is a separate user workstream. Jordanville deferred to post-pilot. Pilot runs on BCP 1979 + LFF 2022 only. Consequence: validation test #3 (cross-tradition surfacing) can't run in pilot and moves to post-pilot alongside Jordanville. Schema's cross-tradition fields get built and lie dormant until then.
2. **Redistribution intent.** — **RESOLVED 2026-04-17:** Corpus is private and staying private. If any part of Commonplace goes public, liturgy is not included. Consequence: no `copyright_status` field needed on `liturgical_unit_meta`. LFF 2022 ingests cleanly under parish/personal use. R3 closed on intent (see risk register).
3. **Psalter duplication policy.** — **RESOLVED 2026-04-17:** Option A — flat `liturgical_unit` rows per translation, grouped by `canonical_id` (e.g., `psalm_023`). Reuses the Rite I/II pattern; no schema additions. A→B (sidetable) stays available as a view if Psalm-grid operations ever matter.
4. **Judge regression tolerance.** — **RESOLVED 2026-04-17:** Moderate, paired with a liturgical surfacing fixture set. Prose: score drift OK; any accept/reject flip triggers review, defensible flips ship with a note, liturgy-spillover flips block. Liturgical fixtures: paired positive/negative cases — positive confirms liturgy surfaces when it should ground the conversation (Marian kenosis → BCP Saint Mary the Virgin collect), negative confirms it *doesn't* surface when context doesn't warrant (technical seed → no mercy-adjacent prayers leak in). The prose harness catches spillover; liturgical negatives catch over-surfacing; together they express the balance — liturgy as ground without inappropriate intrusion. Hinges on actually reviewing every prose flip, no waving through.
5. **`theological_subjects` vocabulary.** — **RESOLVED 2026-04-17:** Controlled list with `_other:<freeform>` escape hatch. Seed with ~25–30 obvious subjects from pilot corpus; tag feasts using controlled + `_other:` freely during population; sweep post-population to promote recurring `_other` tags into controlled and normalize the rest; validator rejects raw uncontrolled tags outside the `_other` namespace once sweep is done. Paired with an MCP tool `subject_frequency` that exposes the audit report so Claude.ai can read + propose promotions, and edit `feasts.yaml` directly via file tools. See §2.6 for tool shape.
6. **Byzantine calendar.** — **Deferred alongside Jordanville (Q1).** Re-open when Jordanville returns to scope. Pilot's calendar resolver is Anglican-only.
7. **Ingestion concurrency.** — **RESOLVED 2026-04-17 (verify, don't decide):** In Phase 1, ingest a small BCP slice first (Collects only, ~170 units) and measure end-to-end wall time. Expected: 2–3 min (single-threaded Ollama + SQLite overhead). If the extrapolated full-book time exceeds ~60 min, optimize before the full run — options are Ollama batch input or a short-lived multi-worker pool. Otherwise do nothing; BCP ingest runs once.

---

## 7. Appendix

### Libraries
| Use | Library | Notes |
|---|---|---|
| HTML parse | BeautifulSoup + lxml | lxml's recovery handles malformed Psalter |
| PDF parse | PyMuPDF (`fitz`) | Font-dict API for LFF structure signals |
| epub parse | ebooklib + lxml | v0.20 active (2025-10-26) |
| Easter/movable feasts | `dateutil.easter` | Already-transitive dep; `EASTER_WESTERN`/`EASTER_ORTHODOX` |
| YAML schema | Pydantic | Matches existing style; swap for jsonschema if already present |
| Token counting | `tiktoken` | Already in pipeline (cl100k_base) |
| Embedding | Ollama `nomic-embed-text` (768-dim) | Already in pipeline; no change |

### Authoritative sources
- **BCP 1979:** https://www.bcponline.org/
- **LFF 2022 PDF:** https://www.episcopalcommonprayer.org/uploads/1/2/9/8/129843103/lesser_feasts_and_fasts_2022_final.pdf
- **General Convention A007 (2022):** https://www.episcopalarchives.org/sites/default/files/gc_resolutions/2022-A007.pdf
- **Lectionary cross-check:** https://www.lectionarypage.net/
- **Jordanville (partial mirrors):** https://holynewmartyrs.org/prayerbook, https://saintjonah.org/services/prayers.htm

### Codebase anchors (paths grounding this plan)
- Schema: `commonplace_db/migrations/0001_initial.sql`, `0003_*` (unique indexes), `commonplace_db/db.py:67–128`
- Chunker: `commonplace_server/chunking.py:20–23` (constants), `:48–81` (main fn)
- Ingest pipeline: `commonplace_server/pipeline.py:46–139` (`embed_document`)
- Handlers pattern: `commonplace_worker/handlers/library.py:35–136`, worker loop `commonplace_worker/worker.py:165–180`
- Search tool: `commonplace_server/server.py:202–274`, `commonplace_server/search.py:50–151`
- Surface/judge: `commonplace_server/surface.py:190–346`, judge rubric `skills/judge_serendipity/SKILL.md`
- ADRs: `docs/decisions/0003-sqlite-schema-design.md`, `docs/decisions/0005-embedding-and-vector-store.md`

### Out-of-scope (for reference)
- BCP 1662, Hymnal 1982, SAPB, Festal Menaion, Triodion, Pentecostarion
- OCA daily captures
- Plainsong Psalter (OCR + pointing)
- Audio
- Full Byzantine Typikon
- Cross-source deduplication beyond Rite I / Rite II within BCP

All come after the pilot validates the schema.

---

## 8. Execution strategy

This section supplements `AGENTS.md` and `docs/execution-plan.md` with plan-specific task decomposition and parallelism. Read those first — they define the operating model (primary holds state, subagents execute per task contract, state in `build/STATE.md`/`build/state.json`, default-parallel with a 5-agent cap and 3-agent sweet spot, subagents never modify state or spawn other subagents).

This section is designed to survive a context clear: a fresh primary reading this doc + `AGENTS.md` + the two ADRs cited in §7 can pick up execution without the conversation that produced the plan.

### 8.1 Task DAG (all phases)

Tasks are numbered `<phase>.<seq>`. Dependencies (`after:`) are hard — downstream tasks cannot start until upstream completes and passes gates. Tasks without a `after:` have no dependencies within the plan.

**Phase 0 — Schema + feast table**
- `0.1` Write migration `0007_liturgical_ingest.sql` (liturgical_unit_meta, feast, commemoration_bio + indexes)
- `0.2` Pydantic schema for `feasts.yaml` + `theological_subjects.yaml`; validator rejects unknown subjects outside `_other:` namespace. `after: 0.1`
- `0.3` `commonplace feast-import` CLI + `make seed-feasts` target; idempotent upsert. `after: 0.2`
- `0.4` `commonplace_server/liturgical_calendar.py` stub: movable-feast helper (`dateutil.easter` + timedelta) + fixed-date lookup against `feast` table
- `0.5` `subject_frequency` MCP tool in `commonplace_server/server.py`. `after: 0.1`
- `0.6` *[user action]* Author `theological_subjects.yaml` starter controlled vocab (~25–30 subjects)
- `0.7` *[user action]* Populate `feasts.yaml` for pilot (~200–300 entries). `after: 0.3`
- `0.8` Unit tests for schema, validator, importer, resolver stub, subject_frequency

**Phase 1 — BCP 1979 parser**
- `1.1` Caching crawler for `bcponline.org` honoring 180s crawl-delay; writes HTML to `~/commonplace/cache/bcp_1979/`. Overnight first run.
- `1.2` Collects parser (Rite I + Rite II, seasonal + proper). `after: 1.1` (but develop against sample pages first)
- `1.3` Daily Office parser (Morning/Evening Prayer, Compline, Noonday, Great Litany). `after: 1.1`
- `1.4` Psalter parser (150 psalms, handles malformed source HTML via lxml recovery). `after: 1.1`
- `1.5` Proper Liturgies parser (Ash Wednesday, Palm Sunday, Triduum, Easter Vigil — speaker tables + inline-styled blocks). `after: 1.1`
- `1.6` Prayers & Thanksgivings parser (1–70, anchor-addressable). `after: 1.1`
- `1.7` `commonplace_worker/handlers/liturgy.py` + `ingest_liturgy_bcp` job kind + worker registration. `after: 1.2` (needs at least one parser)
- `1.8` Add `embed_text_override` seam to `commonplace_server/pipeline.py:embed_document` (for composed-string embedding; §2.7) — independent of parser work.
- `1.9` BCP end-to-end integration test: submit job → worker ingests → `search_commonplace` + `surface` return units. `after: 1.7, 1.8`

**Phase 2 — LFF 2022 parser**
- `2.1` Fetch LFF PDF (pin SHA256), store at `tests/fixtures/lff_2022.pdf`
- `2.2` PyMuPDF font-signal state-machine parser (entry boundary = Sabon-Bold 17pt). `after: 2.1`
- `2.3` Commemoration bio insertion (documents as prose + commemoration_bio row linking to feast). `after: 2.2`
- `2.4` `ingest_liturgy_lff` handler + job kind. `after: 2.2, 2.3`
- `2.5` LFF end-to-end integration test. `after: 2.4`
- `2.6` Cross-check fixtures: ~20 commemorations against `lectionarypage.net` (scripture refs, dates). `after: 2.2`

**Phase 3 — DEFERRED** (Jordanville, post-pilot — §6 Q1)

**Phase 4 — Retrieval integration**
- `4.1` Capture prose regression fixtures: 20 seeds × current candidate pools × current judge outputs; freeze to `tests/fixtures/prose_regression.json`. Runs *before* any SKILL.md edit.
- `4.2` Author liturgical fixtures: ~10 positive cases (theological seed → liturgy should surface) + ~10 negative cases (technical/ordinary seed → no liturgy should surface) as `tests/fixtures/liturgical_surfacing.json`
- `4.3` Edit `skills/judge_serendipity/SKILL.md` with "Liturgical candidates" section + `frame` output field. `after: 4.1`
- `4.4` Extend `search_commonplace` filters: `category`, `genre`, `tradition`, `feast_name`, `date_from`/`date_to` (calendar-range via feast table)
- `4.5` Implement LFF 2022 precedence rules in `liturgical_calendar.py` (extends 0.4 stub). `after: 0.4`
- `4.6` `surface.py` candidate hydration: attach `category`, `genre`, `feast_name`, `tradition` to liturgical candidates before judge invocation
- `4.7` Run prose regression + liturgical fixtures; primary reviews flips per Moderate bar (§6 Q4). `after: 4.1, 4.2, 4.3`

**Phase 5 — Validation**
- `5.1` Test 1 (calendar lookup): "today's collect" with LFF 2022 precedence for 2025 + 2026 sample dates including transfer-rule cases. `after: 4.5`
- `5.2` Test 2 (thematic within-tradition): mercy seed → mercy-themed BCP collect + LFF bio of a mercy-inflected figure. `after: 4.7`
- `5.3` Iterate on `theological_subjects` if 5.2 fails (expected failure mode; schema is fine). `after: 5.2`

### 8.2 Dispatch waves

Wave grouping is the primary's suggested concurrency pattern. Each wave runs concurrently up to the 3-agent sweet spot (5-agent hard cap). Primary can deviate based on actual availability.

| Wave | Concurrent tasks | Notes |
|---|---|---|
| **0A** | `0.1` solo | Migration blocks almost everything in Phase 0 |
| **0B** | `0.2` + `0.4` + `0.5` | Three parallel after 0.1 — at sweet spot |
| **0C** | `0.3` + `0.8` backfilled as each module lands | User action `0.6` + `0.7` happen out-of-band |
| **1A** | `1.1` (overnight background) + `1.8` | Crawler runs long; `1.8` is independent file |
| **1B** | `1.2` + `1.3` + `1.4` against sample HTML | Three parsers; re-run on full cache after `1.1` completes |
| **1C** | `1.5` + `1.6` + `1.7` | `1.7` starts as soon as one parser (usually 1.2 Collects) is gate-clean |
| **1D** | `1.9` solo | Integration test, terminal |
| **2A** | `2.1` + `2.6` (early fetch phase) | Can start during Phase 1 tail if capacity allows |
| **2B** | `2.2` + `2.3` + `2.6` | Three parallel; `2.3` bootstraps on `2.2` skeleton |
| **2C** | `2.4` + `2.5` | Handler then integration test |
| **4A** | `4.1` + `4.4` + `4.5` | Pin fixtures + search filters + calendar precedence — can run during Phase 1/2 tails |
| **4B** | `4.2` + `4.3` + `4.6` | `4.3` after `4.1`, strict |
| **4C** | `4.7` solo | Primary reviews flips |
| **5** | `5.1` → `5.2` → `5.3` | Sequential iteration loop |

### 8.3 Cross-phase overlap

Phases serialize by user mental model but the primary can dispatch across phase boundaries:

- **After Phase 0 completes,** Phases 1 and 2 are fully independent. Both can run concurrently if the primary has budget. Expected overlap: `2.1` + `2.2` launch during Phase 1's parser waves (1B/1C) — LFF PDF extraction has no dependency on BCP.
- **Phase 4 "content-independent" tasks** (`4.1`, `4.4`, `4.5`) can start as soon as their schema-level dependencies land, not gated on full Phase 1/2 completion. `4.1` in particular benefits from early capture while the current judge behavior is still "pure prose."
- **Phase 5 tasks** depend on the full retrieval pipeline; no cross-phase overlap.

Realistic serialized estimate: 14–18 days. Realistic overlapped estimate: 12–17 days. Overlap saves 2–3 days in exchange for heavier primary context during the overlap windows.

### 8.4 Model dispatch

Per task-type, not per task. Picks lowest tier that does the job well per `AGENTS.md`.

| Task type | Model |
|---|---|
| Schema, migration, validator | Sonnet |
| Parsers (HTML/PDF structural reasoning) | Sonnet |
| MCP tool / CLI / handler scaffolding | Sonnet |
| Pipeline seam (`embed_text_override`) | Sonnet |
| Integration tests | Sonnet |
| Judge rubric edit (`4.3`) | Sonnet |
| Fixture capture (prose regression, `4.1`) | Haiku (mechanical — invoke system, record outputs) |
| Fixture authoring (liturgical pos/neg, `4.2`) | Haiku |
| One-time fetches (`2.1` LFF PDF, `2.6` lectionary) | Haiku |
| Primary (dispatch, state, gate validation) | Opus |

Nothing in this plan requires Opus at subagent level.

### 8.5 Validation gates

Every task closes against `AGENTS.md`'s gate list (pytest, ruff, mypy). Task-type-specific adds:

- **Schema (`0.1`):** migration applies cleanly to a fresh DB; `PRAGMA integrity_check` returns `ok`; applied-migration count increments by 1.
- **Validator/importer (`0.2`, `0.3`):** round-trip `feasts.yaml` → import → dump → diff is empty.
- **Parsers (`1.2`–`1.6`, `2.2`):** sample-extraction smoke test — parse 5 known units, compare output shape to fixtures. No manual-tagging paths triggered.
- **Handlers (`1.7`, `2.4`):** job submit → worker claim → document + meta + embedding rows populated; `search_commonplace` returns ingested units.
- **Pipeline seam (`1.8`):** existing embedding paths unchanged (regression); override path produces divergent embedded string.
- **Judge rubric (`4.3`):** prose regression harness passes per Moderate bar (0 un-reviewed flips); liturgical pos cases surface expected units; liturgical neg cases surface 0 liturgical units.
- **Calendar resolver (`4.5`):** 20-date fixture matches `lectionarypage.net` including at least one transfer-rule case.
- **Integration (`1.9`, `2.5`):** end-to-end — submit job, poll status to `complete`, query surfacing, validate.

### 8.6 Context checkpoints

Primary should write a checkpoint summary to `build/STATE.md` and consider handoff to a fresh session at these seams. Target: never exceed ~130K context on the primary.

- **After Phase 0.** Schema + validator + resolver stub + MCP audit tool live. Natural handoff; fresh primary picks up with feast-table population or Phase 1.
- **After BCP parser quartet (`1.2`–`1.5`) lands.** Primary context will be parser-heavy; good seam before handler/integration work.
- **After Phase 1 integration test (`1.9`) passes.** BCP fully ingested. Clean handoff before LFF.
- **After Phase 2 integration test (`2.5`) passes.** Both corpora live. Clean handoff before retrieval work.
- **After `4.3` SKILL.md edit merges.** Judge rewritten. Clean handoff before Phase 5 validation.

At each seam: update `STATE.md` phase progress, completed tasks, open questions, known discoveries. The next primary reads `STATE.md` + this doc + `AGENTS.md`.

### 8.7 Definition of Done per phase

- **Phase 0:** migration `0007` applied; `feasts.yaml` imports cleanly with ≥200 rows; calendar resolver returns correct date for 5 fixed-date + 5 movable-feast 2026 test cases; `subject_frequency` MCP tool returns expected JSON shape.
- **Phase 1:** BCP ingest job completes; `SELECT COUNT(*) FROM liturgical_unit_meta WHERE source='bcp_1979'` ≈ 600 ±5%; `search_commonplace(content_type='liturgical_unit', source='bcp_1979', limit=3)` returns plausible units.
- **Phase 2:** LFF ingest job completes; ~400 liturgical_unit rows (collects, both rites) + ~200 commemoration_bio rows; cross-check sample of 20 commemorations matches `lectionarypage.net`.
- **Phase 4:** prose regression harness shows 0 un-reviewed flips; liturgical pos fixtures surface expected units; liturgical neg fixtures surface 0 liturgical units; search filter extensions return expected filtered results.
- **Phase 5:** Test 1 returns correct collect for 2025 + 2026 sample dates (including one transfer-rule case); Test 2 surfaces both expected candidates on a mercy seed. Test 3 remains deferred with Jordanville.

### 8.8 Pre-flight before Phase 0

Per `docs/execution-plan.md`:
- `state.json` readable and current
- Git working directory clean
- `make smoke` passes (existing system healthy)
- No in-progress tasks from a prior session
- Open questions from §6 all marked RESOLVED (confirm: Q1–Q7 are all resolved except Q6 which is deferred)

If any fails, primary surfaces before dispatching `0.1`.

