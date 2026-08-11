# PR Risk Copilot

A service that reviews GitHub pull requests for **structural risk** — not style
or lint issues, but "does this change touch something fragile that isn't
obviously fragile from the diff alone." It grounds an LLM's assessment in
real codebase context (call graphs, test coverage, past incident patterns)
instead of asking a model to review a diff in isolation with no context.

**Status: in progress.** This is being built incrementally and documented
honestly as it goes — see [Current status](#current-status) below for what's
actually working today versus what's still ahead. The full pipeline (webhook
→ retrieval → grounded LLM assessment → posted comment) is real and
live-verified end to end; the offline evaluation (below) is done and the
live demo pass is the one remaining item.

## Why this exists

Most "AI PR reviewer" demos ask an LLM to comment on a raw diff. That's the
naive version every tutorial stops at, and it produces generic commentary
that could apply to almost any change. The interesting engineering problem
is retrieval: pulling in *who calls this function*, *is it covered by
tests*, and *has something like this broken before* — then forcing the model
to reference that context specifically rather than hedge in general terms.

## Architecture

```
   GitHub PR event (webhook)              Scheduled job (every 15 min)
   (opened, synchronize)                  pulls latest main
            |                                        |
            v                                        v
   +-------------------+                  +----------------------+
   |  Webhook Receiver   |                  |  Embedding Indexer    |
   |  (validates sig,     |                  |  (diffs against last-  |
   |   enqueues, 200 fast)|                  |   indexed SHA, re-     |
   +---------+----------+                  |   embeds changed funcs)|
             |                              +-----------+------------+
             v                                          |
   +-------------------+                                v
   |   Queue (Redis      |                    +-------------------+
   |   Streams)           |                    |   PostgreSQL +      |
   +---------+----------+                    |   pgvector           |
             |                                |  (code chunks,       |
             v                                |   call graph,        |
   +---------------------------+              |   past-incident       |
   |   Worker: PR Processor      |------------>+   tags)                |
   |  1. Fetch diff via GitHub    |   (reads for  +-------------------+
   |     API                      |    retrieval)
   |  2. Parse changed             |
   |     functions/files (tree-    |
   |     sitter, multi-language)   |
   |  3. Query pgvector for        |
   |     related context           |
   |  4. Build grounded prompt     |
   |  5. Call LLM: Ollama primary, |
   |     hosted API on failure     |
   |  6. Post comment via          |
   |     GitHub API                |
   +---------------------------+
```

The webhook receiver does almost nothing except validate and enqueue — it
returns `200` immediately, and all real work happens asynchronously in the
worker. This is what prevents GitHub's webhook delivery system from timing
out and retrying into a duplicate-processing storm.

## Current status

| Piece | Status |
|---|---|
| Dev environment (Docker/Postgres+pgvector/Redis, Ollama, GitHub App) | ✅ Done |
| Webhook receiver: HMAC verification, repo allowlist, dedup, fast-ack | ✅ Done |
| Worker-side debounce/supersede handling for rapid pushes | ✅ Done |
| Diff fetcher (paginated) + function-level parsing | ✅ Done — **Python, JavaScript/TypeScript, Go, Java**, via a per-language tree-sitter registry |
| Retrieval layer (embeddings, call graph, incident tags) | ✅ Done |
| LLM risk assessment (Ollama + Gemini fallback) | ✅ Done |
| GitHub comment posting | ✅ Done — live-verified on a real PR |
| Offline evaluation against labeled historical PRs | ✅ Done — see [Evaluation results](#evaluation-results) |
| Live demo (2-3 real PRs, posted comments) | ⏳ Not yet run |

Every phase so far has real tests behind it — duplicate webhook delivery,
concurrent PRs, debounce/supersede, function-level diff parsing, retrieval,
LLM assessment/fallback, and comment posting are all verified against
running infrastructure (Redis, Postgres+pgvector, Ollama) and real
historical GitHub PRs (not just mocks), including PRs in `psf/requests`,
`gin-gonic/gin`, `google/gson`, and `nestjs/nest`.

### Embedding freshness

The reindex job (`scheduler/reindex_job.py`) re-embeds each tracked repo
against its latest default-branch commit every 15 minutes (`REINDEX_INTERVAL_MINUTES`),
diffing against the last-indexed SHA so only changed functions are
re-embedded. Retrieved context is therefore **at most 15 minutes stale** by
design; a live run against the pinned eval repo measured actual staleness
at 2m57s end-to-end, comfortably inside that bound (SPEC N5).

## Language support

The diff parser identifies which functions/methods a diff actually touches
— not just which lines — using tree-sitter instead of regex, because regex
can't reliably tell where a function body ends, and can't handle a change
that lands only in a multi-line signature or only on a decorator/annotation
line. Supported today: **Python, JavaScript, TypeScript, Go, Java**.

Adding another language is a matter of extending the registry in
`worker/diff_parser/languages.py` with that language's grammar and which
node types count as a function/method — the parsing logic itself
(`worker/diff_parser/parser.py`) is language-agnostic. A file in an
unsupported language is skipped, not an error; the rest of the PR is still
reviewed.

## Evaluation results

Offline eval (SPEC §8): 12 real historical PRs from `psf/requests`
(`eval/labeled_prs.json`, 7 labeled risky / 5 labeled safe against ground
truth established independently — 6 are documented CVE/bug fixes, 1 is a
direct follow-up change to a just-fixed CVE function), run through
retrieval + both LLM backends offline, no comments posted
(`eval/run_eval.py` → `eval/results.json`). Numbers below are from a clean,
isolated final run — not cherry-picked, and not the first run (see "what
the first pass exposed" below for what changed and why).

**Retrieval quality:** correctly surfaces the exact expected incident tag
for **all 7 of 7** tagged PRs. This wasn't true on the first pass (4/7) —
3 PRs (`#6028`, `#2896`, `#4718`) predate this repo's migration to a `src/`
layout, so their historical file path (`requests/utils.py`) didn't
exact-string-match the current index's path (`src/requests/utils.py`).
Fixed in `worker/retrieval/query.py`: the "own function" DB lookup now
falls back to a path-boundary suffix match when the exact path misses (a
`/`-anchored suffix check, not a raw substring match, so `myrequests/x.py`
can't falsely match a query for `requests/x.py`) — verified both with unit
tests and by re-running retrieval directly against the 3 real affected PRs.

**Ollama vs. Gemini — the actual tradeoff:**

| | Ollama (`qwen2.5-coder:1.5b`) | Gemini (`gemini-3.1-flash-lite`) |
|---|---|---|
| Precision / Recall | 1.0 / 0.75 | 0.67 / 1.0 |
| Failures | 0 (isolated run) | 0 |
| Latency (successful calls) | 8-21s, mean 13s | 1.7-7.7s, mean 3.0s |
| Avg prompt / output tokens | ~1685 / ~158 | ~1957 / ~274 |
| Cost | $0 (local) | $0 (free tier) |

Neither backend is simply "better" — they fail in opposite, specific ways.
Ollama never produces a false positive but still misses 2 of 8 real risky
PRs. Gemini never misses a real risk and never times out, but **never once
returned `"low"` across the entire 12-PR set** — every safe PR still came
back `medium`/`high`. That's not "Gemini is more cautious," it's a real
calibration gap: a backend that always says "at least medium" gives the
same signal on every PR regardless of actual risk, which undermines the
recall number's face value.

**What the first pass exposed, and what actually fixed it:** the first eval
run showed Ollama timing out on 3/12 PRs at the 30s N4 budget, run
concurrently with the full test suite in the background. Re-running in
isolation dropped that to **0 timeouts**, latency roughly halved (mean 22s
→ 13s) — most of the original timeout risk was this 8GB dev machine's CPU
contention, not prompt size alone. Real prompt size still matters
independently, though: `worker/llm/assess.py`'s primary Ollama attempt now
also trims the semantic-neighbor section for any PR touching more than one
function (previously trimming was fallback-only, per SPEC §7 as written) —
single-function PRs, never the ones at risk, are unaffected. Both the
isolated-run finding and the trimming change are documented in
PROBLEMS.md/SPEC.md rather than just quietly making the number look better.

**End-to-end latency (N1, retrieval + Ollama path, isolated run):** p50 =
15.7s, p90 = 19.8s, max = 27.2s — n=12. Comfortably under the 30s budget
once run without competing for CPU.

**Ground-truth corrections made during this eval** (all fully logged in the
local problems log, not silently patched): a near-miss where fabricated
commit SHA tails almost shipped in the labeled set, caught before running
anything against them; and one PR (`#6716`) originally labeled safe that
turned out, on tracing the real diff, to modify the exact
CVE-2024-35195-tagged function from a PR merged 3 days earlier — relabeled
risky, and the full eval re-run clean against the corrected set.

**Live demo (SPEC F5, reported separately per §8):** 3 real PRs opened on
this repo, full pipeline run for real via `worker.review.review_pr()`, all
3 posted comments independently re-fetched via the GitHub API and
confirmed. One surfaced a third, distinct concrete example of Ollama's
grounding-accuracy limitation: a comment claimed a plain getter function
"has a documented incident history" as a positive signal, when the actual
prompt sent explicitly said `Incident history: none` — the model didn't
misread a fact, it invented one, backwards relative to its own system
instructions. Logged in full in PROBLEMS.md.

## Tech stack

| Layer | Choice |
|---|---|
| Service (receiver, worker) | Python (FastAPI) |
| Queue | Redis Streams |
| Vector store | PostgreSQL + pgvector |
| Code parsing | tree-sitter, multi-language registry |
| LLM | Ollama (local, primary) + Google Gemini API free tier (fallback) |
| Package manager | uv |
| GitHub integration | REST API + webhooks, GitHub App (not a PAT) |

## Local development

Requires: [uv](https://docs.astral.sh/uv/), Docker, [Ollama](https://ollama.com).

```bash
# 1. Install dependencies
uv sync

# 2. Start Postgres+pgvector and Redis
docker compose up -d

# 3. Pull the local LLM (model name is configurable, see .env.example)
ollama pull qwen2.5-coder:1.5b

# 4. Configure secrets
cp .env.example .env
# fill in GITHUB_APP_ID, GITHUB_WEBHOOK_SECRET, GEMINI_API_KEY, etc.

# 5. Run the tests (fast suite — no network calls, safe to run repeatedly)
uv run pytest -v

# ...or include the real-historical-PR live tests too (hits the GitHub API,
# subject to its 60/hr unauthenticated rate limit — run deliberately, not
# on every iteration)
uv run pytest -m network -v

# 6. Run the webhook receiver
uv run uvicorn receiver.main:app --port 8000
```

For local webhook delivery during development (GitHub can't reach
`localhost` directly), forward a [smee.io](https://smee.io) channel to the
receiver:

```bash
npx smee-client --url <your-smee-channel> --target http://127.0.0.1:8000/webhooks/github
```

## Testing philosophy

Tests in this repo hit real infrastructure where it matters, rather than
mocking everything: the webhook/dedup/debounce tests run against an actual
Redis instance (a dedicated test DB, not mocked), and the diff parser is
verified against real historical PRs pulled live from GitHub — in
`psf/requests`, `gin-gonic/gin`, `google/gson`, and `nestjs/nest` — with
ground truth independently checked by reading the actual patch text before
writing assertions, not just trusting the tool's own first output.

The real-PR tests (8, across diff parsing, indexing, and retrieval) are
marked `network` and excluded from the default test run, since repeatedly
hitting them during normal iteration exhausts GitHub's unauthenticated rate
limit (60 requests/hour) fast. Run `uv run pytest -m network` to include
them deliberately.
