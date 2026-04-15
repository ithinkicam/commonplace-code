# Commonplace

A personal commonplace book and reading companion. Everything you read, watch, capture, and think gets funneled into a single searchable corpus. Claude uses it to surface forgotten passages, connect ideas across your reading, and triangulate across the frameworks you hold — Socratically, in service of whatever question you're working.

This document is the single source of truth for the design. It supersedes all prior planning documents.

---

## Design rules

- **Capture is share-sheet simple.** No tagging, no categorizing, no required metadata. You are the curator. The system organizes.
- **Synthesis is for Claude, not you.** Internal reference material. Written for retrieval, token-efficient, never an artifact to browse.
- **Socratic by default, interlocutor on engagement.** Claude hands you the passages and asks what you see; thinks alongside you when you pick it up.
- **Recency is not a signal.** When you encountered an idea doesn't matter. What the idea is does.
- **Ambient by default, never intrusive.** Surfacing happens inside real conversations, capped at two per chat, with a quality filter.
- **Local and private by default.** Embeddings stay on your machine. Only Claude API sees synthesis-time excerpts.
- **Deterministic plumbing, model for reasoning.** Ingestion, storage, and retrieval are Python. Synthesis and judgment are Claude Code invoking skill files.

---

## Architecture at a glance

Two services on a Mac mini M1, both under `launchd`. All access via your private Tailscale tailnet.

```
Your devices                                Mac mini (tailnet only)
────────────                                ──────────────────────────

Claude app on phone/desktop ─── MCP ─────►  commonplace-server
                                            ├─ FastMCP (tools for Claude)
Phone/tablet share ─── HTTPS ───────────►   ├─ /capture endpoint
                                            ├─ /healthcheck
                                            └─ Job queue (SQLite)

                                            commonplace-worker
                                            ├─ Ingestion handlers
                                            ├─ Job dispatcher
                                            ├─ Spawns: claude -p <skill>
                                            └─ Embeds via Ollama

                                            Shared storage
                                            ├─ ~/commonplace/ (vault)
                                            ├─ library.db (SQLite + sqlite-vec)
                                            └─ skills/ (synthesis prompts)

Day One app ─── Day One MCP ───────────►    (local, queried by Claude)
```

The MCP server and the capture endpoint live in one process because FastMCP exposes HTTP routes alongside MCP tools. The worker stays separate so long jobs don't block requests.

**Claude Code is the execution engine for synthesis.** When a synthesis job runs — book note regen, profile regen, serendipity judging, capture summarization — the worker invokes `claude -p` with the right skill file and context. The subscription absorbs the cost. There is no API fallback; when Claude usage caps hit, jobs wait.

---

## The cockpit (three tiers)

Bio, perennials, and operational calibration live in three different places, each suited to what it is.

### Tier 1 — Claude memory (bio facts)

Stable facts Claude needs for every interaction. Stored via the memory system in each project you use. Updated when you tell Claude to, not programmatically. Examples:

- pronouns
- platform/device facts
- current situation context (medical leave, subscription tier)
- low vision / audiobook-first reading pattern

Project-scoped, so memory is per-project. The Commonplace profile (tier 3) compensates by loading globally via MCP.

### Tier 2 — Claude preferences (perennials)

Stable intellectual commitments. Frameworks held, identity threads, recurring lenses. Stored in Claude.ai preferences so they load in every chat, every project, whether or not Commonplace is connected. Manually maintained; never auto-regenerated. A mirror at `~/commonplace/profile/perennials.md` lets the local worker see them for synthesis jobs.

The format is directive, not biographical — *"When engaging with X, know Y applies"* — so Claude treats them as always-relevant guidance rather than topical context.

### Tier 3 — Commonplace profile (operational)

Loaded via `get_profile()` at chat start. ~500 tokens, capped. Regenerated monthly or on demand. Contains:

- **How to talk to me** — register, pacing, voice, what to avoid
- **What I'm sensitive about** — topics/framings requiring care beyond pronouns (those are in tier 1)
- **How I think** — inferred operational patterns (from corpus and chat history)

Each item tagged `[directive, YYYY-MM-DD]` (user-authored, sacred) or `[inferred]` (Claude's read, updated by regen). Corrections in chat promote inferred items to directives immediately via `correct(target='profile', ...)`.

**What's deliberately NOT in this profile:** perennials (tier 2), bio facts (tier 1), live questions (inferred from recent activity at query time, not stored), book-by-book engagement detail (lives in book notes).

---

## Data sources and how they enter

### Automated — you do nothing

| Source | Mechanism | Frequency |
|---|---|---|
| Day One journal | Official Day One MCP, queried live | Real-time |
| Bluesky posts and thread replies | atproto nightly pull; replies <30 chars dropped | Nightly |
| Kindle highlights | read.amazon.com scraper; covers phone-app and device (both sync to cloud) | Nightly |
| PDF/epub library | Watched folder on local filesystem (Google Drive synced via Drive for Desktop) | On file change |

Readwise is documented as an escape hatch if the Kindle scraper breaks more than twice in six months.

### Share-sheet — you tap share

Android via HTTP Shortcuts, iPad via Apple Shortcuts. Both POST to `https://<mini>.ts.net/capture` with a bearer token from OS keychain.

| Input | Handler |
|---|---|
| YouTube URL | yt-dlp → existing captions; Whisper-medium fallback if quality is poor |
| Podcast URL | RSS check for `<podcast:transcript>`; Whisper-medium fallback |
| Bluesky URL | atproto fetches post + full thread |
| Article URL | Trafilatura reader-mode |
| Image / screenshot | Tesseract OCR; image preserved |
| Video file | ffmpeg → Whisper for audio; keyframe OCR for text overlays |
| Plain text | Embedded directly |

### Said-to-Claude — you ask

One tool, `save_note(text, type?)`, handles quick written notes, chat summaries, and profile additions. Type hint differentiates the storage path.

### Bulk paste — pinned Haiku chat

A fresh chat on claude.ai, model set to Haiku, pinned for reuse. First message establishes capture behavior:

> Everything I paste in this chat is a capture for Commonplace. For each paste, call `save_note` with the content and acknowledge with a single short line. Do not expand, summarize, quote back, or ask reflection questions. Just save and wait. This stays true for the entire chat.

When instruction drift becomes noticeable after very long use, start a new chat with the same first message. Low-cost restart.

### Explicitly out of scope

- Instagram Reels (yt-dlp unreliable without auth; auth risks account). Screen-record and share video file instead.
- Voice notes (unused).
- Partial captures (timestamp ranges, passage selections).
- Tagging at capture time.
- Auto-importing full Claude chat history. Only explicitly saved chats enter.

---

## Storage layout

```
~/commonplace/
├── books/<slug>/
│   ├── meta.yaml
│   ├── source.epub|.pdf            (when available)
│   ├── highlights.md
│   ├── notes.md                    (Claude's reference notes)
│   └── quotes.jsonl
├── captures/YYYY/MM/
│   ├── <timestamp>-<slug>.md       (frontmatter + content)
│   ├── images/
│   └── videos/
├── bluesky/posts.jsonl
├── profile/
│   ├── current.md                  (tier 3 operational)
│   ├── perennials.md               (mirror of tier 2 preferences)
│   ├── inbox/                      (cross-chat profile additions)
│   └── history/                    (snapshots)
├── library.db                      (SQLite + sqlite-vec)
├── skills/                         (versioned synthesis prompts)
│   ├── classify_book/SKILL.md
│   ├── generate_book_note/SKILL.md
│   ├── regenerate_profile/SKILL.md
│   ├── judge_serendipity/SKILL.md
│   ├── summarize_capture/SKILL.md
│   └── reconcile_book/SKILL.md
└── .git/
```

Two separate GitHub remotes: `commonplace-code` (public-safe: MCP server, worker, schemas, skill files) for collaboration; `commonplace-vault` (private: notes, profile, connections, corpus) for backup only. Optional: encrypt `profile/` and `connections/` at rest with `gocryptfs` if threat model warrants it later.

Obsidian can open `~/commonplace/` as a vault on your Mac for browsing. No sync configured — canonical copy lives on the Mac mini.

---

## Synthesis

Three back-end jobs, all implemented as skill files invoked via Claude Code. Worker code doesn't contain prompts — just dispatches jobs to skills with context.

### Profile

Monthly cron runs `regenerate_profile` skill. Reads current profile + inbox additions + sampled recent corpus signal. Updates only the `[inferred]` items; directives are preserved verbatim. Snapshots previous version to `profile/history/`.

Cross-chat seeding: `save_note(text, type='profile_addition')` lands content in `profile/inbox/` for next regen to integrate. Corrections via `correct(target='profile', ...)` update directives immediately.

Directives untouched 12+ months get a "still accurate?" check surfaced once in chat. You confirm or retire.

### Book notes

Every book you own or have logged gets a note. No read/unread gate. Duplicates resolve to single canonical book via normalization (OpenLibrary OLID preferred, fuzzy title+author otherwise).

**Three templates chosen at ingest:**

- **Argument** (theology, philosophy, critical theory): what it is / key moves / your engagement / how it connects
- **Narrative** (fiction, memoir, narrative nonfiction): what it is / threads / your engagement / how it connects
- **Poetry** (lyric collections): sense / figures and preoccupations / voice / poem anchors / your engagement

Edge cases default to argument; corrections in chat adjust.

**Generation strategy by Claude's knowledge level:**

Library audit shows ~34% HIGH / ~31% MEDIUM / ~28% LOW across the combined library.

- **HIGH** (canonical, well-known): skip structural content; store sources + your engagement + connections. Rely on training data at query time.
- **MEDIUM/LOW with full text**: generate structural content from actual file at ingest. Where Commonplace's real value lives.
- **MEDIUM/LOW without full text** (audiobook-only, Libby, file-less StoryGraph): lightweight note fleshed out through chat engagement.

**Regen triggers:** weekly cron for books with new material, after `save_note(type='chat_summary')` if the saved chat mentions a book, on-demand via `regenerate(target='book', target_id=<slug>)`. Steady-state volume is low (1-3 books per week at most).

Corrections via `correct(target='book', ...)` preserved across regens.

### Capture summaries

Long captures (over ~2000 words) get a short summary at ingest: one-paragraph description, 5-8 bullet points of key claims, a few verbatim quotes. Summary is embedded for search; full transcript is kept and available via `search_commonplace`. Haiku via skill invocation.

---

## Serendipity

The behavior that delivers primary value. Socratic, ambient, capped at 2 per chat.

### Two surfacing types

- **Connection.** A passage from your corpus sitting next to what you're discussing. *"You highlighted this in Gawande — does it sit next to your current question?"*
- **Triangulation.** Multiple passages from across perennials you hold, each with purchase on the current question. *"Plato here, Aeschylus here, Augustine here — how do they sit together for you?"*

### Two modes

- **Ambient.** Claude scans silently during substantive chats, surfaces when something genuinely fits.
- **On-demand.** You invoke explicitly: *"Let's talk about theories of love in Phaedrus and Eros the Bittersweet."* Claude grounds in your highlights and notes first, then expands with its own knowledge, flagging stretch material when blending.

### Seeding

Always the current chat topic. No cold pulls. Depth bias: "you haven't engaged with this in a while" is a small positive signal in ranking. This is the engine behind the *"oh, I'd forgotten that"* reaction.

### Two-pass filter

1. Vector search returns top ~10 candidates (via sqlite-vec, local).
2. Threshold gate: if none pass similarity floor, skip silently.
3. Judge pass via `judge_serendipity` skill on Haiku. Rejects shallow thematic matches.
4. Cap: at most 2 per chat.

### Directive-based learning

When Claude surfaces something and you react (*"good pull" / "shallow, skip stuff like that"*), Claude adds a directive to the judge skill's context (something like *"prefer candidates that make a real connective claim, not just shared vocabulary"*). Accumulated directives get folded in at next skill file update. No separate feedback subsystem to maintain.

---

## MCP tool surface

Consolidated to what's needed. Each tool description under ~100 tokens, lead with trigger condition.

| Tool | Purpose |
|---|---|
| `search_commonplace(query, filters?)` | Semantic search across books, highlights, captures, Bluesky. Filters: type, source, date range. |
| `get_book(slug)` | Full book record: metadata, highlights, note, engagement |
| `list_books(filter?)` | Browse library |
| `surface(seed, types?, limit?)` | Ambient and on-demand serendipity |
| `save_note(text, type?)` | Captures, profile additions, chat summaries |
| `correct(target_type, target_id, correction)` | On-the-fly corrections for profile or book notes |
| `regenerate(target_type, target_id?)` | Triggers regen for profile, a book, or a capture |
| `add_book(title, author)` | For Libby audiobooks and other uncapturable books |
| `merge_books(book_ids)` | Manual rescue for fuzzy-match edge cases |
| `submit_job(job_type, params)` | Phone-orchestrated long-running jobs (book note batch, ingest, etc.) |
| `get_job_status(filter?, job_id?)` | Job queue inspection |
| `cancel_job(job_id)` | Abort a running job |
| `reload_prompts()` | Hot-reload skill files after editing |
| `healthcheck()` | Services, inbox depth, last successful jobs, rot detection |

Fourteen tools total.

---

## Reliability

**Process lifecycle.** `launchd` with `KeepAlive=true` and `RunAtLoad=true`. Both services start on boot and restart on crash. A supervisor watches Claude Code subprocesses and kills ones running past expected duration.

**Data durability.** SQLite in WAL mode with `synchronous=NORMAL`. Markdown writes are atomic (`.tmp` + fsync + rename). Schema migrations built in from day one via a version table; adding columns is easy, removing them is deliberate.

**Job resumability.** Every ingestion and synthesis job writes stage-level checkpoints — not just "last book processed" but "download done / transcript started / transcript done." Power loss mid-Whisper resumes from last completed stage. Capture inbox is a directory of files; dedup by content hash prevents replay duplicates.

**External drive handling.** If library lives on external HDD: mount check before library operations. If offline, library jobs log and skip; metadata and embeddings on internal storage so everything else works.

**Rot detection.** Healthcheck surfaces proactive warnings: "scraper hasn't produced output in 48h," "no jobs picked up in 1h," "embeddings haven't run this week," "book notes table hasn't changed in 10d." Claude reports these when asked about health, and a weekly notification (email or ntfy.sh) surfaces unprompted.

**Backup.** Nightly `git commit` of vault to private remote. Weekly `sqlite3 .backup` of `library.db`, compressed, separate backup (not same GitHub remote as code).

---

## Security and privacy

**Private Tailscale tailnet only.** Tailscale installed on phone and iPad. No public exposure. Bearer token on capture endpoint as backup auth, stored in OS keychains (Android Keystore, iOS Keychain, macOS Keychain), rotated once after Phase 1 stabilizes.

**Rate limits on capture endpoint.** 100 captures/min, 50MB/capture.

**Local embeddings via Ollama `nomic-embed-text`.** Private content (journal, highlights, captures) never leaves the Mac mini for embedding. Pinned versions for both Ollama and the embedding model — swapping models means re-embedding the corpus.

**Claude Code runs as a dedicated restricted user** on the Mac mini with filesystem scope limited to the vault, MCP code, and what's needed. Secrets (bearer tokens) in macOS Keychain, not in files Claude Code might scan.

**Split GitHub repos.** `commonplace-code` is public-safe (no personal content). `commonplace-vault` is a separate private backup remote, not colocated with code. Profile and connections directories optionally excluded from vault backup and encrypted at rest if your threat model warrants.

**Accepted trust placement:** All synthesis and surfacing content passes through Anthropic's API/chat interface. Their terms are appropriate for this use, and you've named it explicitly as the trade you're making for what this system does.

---

## Cost

Flat-rate, tied to your Claude plan. No variable API billing.

| Phase | Cost |
|---|---|
| Max month (April 2026) | $200 (already committed) |
| Post-Max steady state (Pro) | $20/month |
| Tailscale private tailnet | $0 |
| Local embeddings | $0 |
| GitHub private repos | $0 |

Initial ingest is absorbed during the Max month. Ongoing synthesis load is low enough (1-3 books/week with new material, occasional capture summaries, monthly profile regen, ambient serendipity during chats) that Pro comfortably handles it. Failure mode if usage caps hit is "wait for reset," not "spill to API billing."

If Readwise becomes necessary later: +$8/month.

---

## Build phases

Revised timeline assumes Claude Code doing most of the coding against this spec. Realistic total during Max month: 3-4 weekends of focused work. Stagger build sprints and ingest sprints across separate 5-hour windows to avoid competing for your allowance.

### Phase 0.0 — Build the build system (half session)

Before any Commonplace code, the agent execution scaffolding has to exist. Fully specified in `commonplace-phase-0-0.md`. Produces both GitHub repos, `STATE.md` and `state.json` templates, `AGENTS.md`, `Makefile`, tests scaffold, CI config, `safe-mode.sh` panic button, first ADR, and a self-test verifying the agent execution loop works end-to-end.

Skipping this phase means agents improvise the rails as they go. Don't.

### Phase 0 — Setup (half weekend)

- Pin Claude Code version; test `claude -p` non-interactive mode behavior
- Tailscale installed and working on phone, iPad, Mac mini
- Create dedicated `commonplace` user on Mac mini with scoped home
- Install Ollama + `nomic-embed-text`, pin versions
- Confirm Drive for Desktop is installed and books folder is syncing locally
- Initialize two GitHub repos (`commonplace-code`, `commonplace-vault`)
- Set up OS keychain entries for secrets
- Confirm Day One CLI + MCP access

### Phase 1 — Foundation (1 weekend)

- `commonplace-server` skeleton: FastMCP + `/capture` + `/healthcheck`
- `commonplace-worker` skeleton with launchd config
- SQLite schema + migration system
- Job queue tables and `submit_job` / `get_job_status` / `cancel_job` tools
- Android HTTP Shortcut + iPad Shortcut configured
- Memory edits set via Claude memory tool
- Perennials drafted and added to Claude preferences
- Day One MCP connected to Claude
- First round-trip test: capture from phone → inbox → worker → vault

### Phase 2 — Ingestion (1 weekend)

- Bluesky historical pull + embedding pipeline
- Library watched-folder on local filesystem (Drive for Desktop handles Drive→disk sync)
- Kindle scraper with version-pinning, loud alerts, email-export fallback
- External drive mount checks (if applicable)
- StoryGraph one-time CSV seed
- Book classification skill (`classify_book/SKILL.md`)
- Book note generation skill, three templates
- Begin overnight batch: Claude Code generates notes for MEDIUM/LOW books with text

### Phase 3 — Capture handlers (1 weekend)

- YouTube, podcast, Bluesky URL, article, image, video file handlers
- Caption quality detection and Whisper fallback
- Capture summary skill for long content
- Pinned Haiku chat configured for bulk paste
- Unified `search_commonplace` wired across all content types

### Phase 4 — Synthesis and serendipity (1 weekend + ongoing)

- Profile regen skill + scheduled monthly cron
- `correct` tool with on-the-fly directive promotion
- Serendipity judge skill + feedback directive mechanism
- `surface` tool wired to Claude's custom instructions
- First month of heavy use = real prompt tuning. Expect this. Budget attention.

### Phase 5 — Media indexing (deferred)

Plex + audiobookshelf pulls only after the core system has proved itself for 2-3 months. Value of this tier is speculative; don't build until you can point to specific moments where it would have helped.

---

## Known weaknesses

Named honestly rather than papered over.

**Kindle scraper will break periodically.** Amazon changes their HTML. Version pinning, alerts, email-export fallback cover you, but eventual manual intervention is inevitable. Readwise is the escape hatch if it breaks twice in six months.

**StoryGraph has no API.** One-time CSV seed at Phase 2, no ongoing sync. New ratings enter via `add_book` or `correct` in chat.

**Claude Code non-interactive mode is evolving.** Pin versions; upgrade deliberately after testing. First unexpected behavior you hit will cost real debug time.

**Ambient serendipity requires real prompt iteration before it feels right.** At launch, the judge will misfire. Budget Phase 4 + first month of use for tuning via directive accumulation. If you don't iterate, surfacing feels flat and the system's primary value collapses.

**Book classification will have edge cases.** Lyric-essay, memoir-as-philosophy, argument-with-literary-mind — these will occasionally mis-template. Reactive correction via `correct` tool; no proactive validation.

**HIGH-knowledge books may not actually be skipp-able.** The assumption that Claude's training is adequate for HIGH books is untested at depth. If early conversations feel gestural, the threshold drops and more books get generated notes.

**Three-tier cockpit is clean architecturally, untested operationally.** The memory/preferences/profile split may cause fragmentation feel in practice. Adjust based on first-month experience.

**Mac mini is a single point of failure.** Vault in git is recoverable; embeddings + local Ollama state must be rebuilt if hardware dies. Weekly backup of `library.db` mitigates but doesn't eliminate the recovery cost.

**Android background handling.** Doze mode can silently drop HTTP Shortcuts retries. If this bites in practice, mitigation is a persistent notification keeping the app foreground-ish. Annoying but reliable.

**This is an ambitious first build.** There's a smaller system you could build in 2-3 weekends and grow from. You've chosen the full path. First month of real use will be "fixing first-month bugs while using it," not "enjoying the finished product." Plan accordingly. Phase 1 alone gives you a meaningful improvement; don't treat the full scope as pass/fail.

---

## Locked decisions

Recorded so we don't revisit:

- Name: Commonplace. Role: reading companion / research partner.
- Three-tier cockpit: memory (bio) / preferences (perennials) / MCP profile (~500-token operational).
- Two services: `commonplace-server` (FastMCP + HTTP) and `commonplace-worker`.
- Synthesis as skill files invoked via Claude Code `claude -p`. No API fallback.
- Phone-orchestrated job queue with `submit_job` / `get_job_status` / `cancel_job`.
- Local embeddings (Ollama `nomic-embed-text`, pinned version).
- Private Tailscale tailnet, no public Funnel.
- Split GitHub repos: code public-safe, vault private backup.
- Restricted user for Claude Code, secrets in macOS Keychain.
- Three book note templates: argument / narrative / poetry; default to argument on edges.
- Serendipity: Socratic, ambient + on-demand, 2-per-chat cap, Haiku judge, directive-based learning.
- No read/unread tracking. Library is a searchable corpus.
- Day One via official MCP, not ingested.
- Bluesky: own posts + thread replies with length filter.
- Kindle: scraper; Readwise as documented escape hatch.
- StoryGraph: one-time seed only.
- Pinned Haiku chat for bulk paste (not a dedicated Project).
- Obsidian optional for desktop browsing of vault.
- Media indexing (Plex, audiobookshelf) deferred to Phase 5.
- Directive-based learning, no formal feedback-loop subsystem.
- ~14 MCP tools.
- Two cron jobs total: daily ingest, periodic synthesis.
- Schema migrations from day one.
- Skill files version-controlled in `commonplace-code`; hot-reloadable via `reload_prompts()`.

---

## Phase 0 inputs (all resolved)

1. **Library location.** Google Drive folder synced locally to the Mac mini via Drive for Desktop. Ingest path reads the local filesystem — no Drive API client needed, no OAuth setup. The sync client handles the Drive-to-disk mirroring.
2. **Readwise.** Not at start. Kindle scraper is the path. Readwise is the documented escape hatch if the scraper breaks more than twice in six months.
3. **Encryption at rest for profile/connections.** No. FileVault on the Mac mini is sufficient for the threat model. Revisit only if threat model changes.

Everything is ready to build.
