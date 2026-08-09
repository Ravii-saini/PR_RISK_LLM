# PR Risk Copilot

A service that reviews GitHub pull requests for **structural risk** — not style
or lint issues, but "does this change touch something fragile that isn't
obviously fragile from the diff alone." It grounds an LLM's assessment in
real codebase context (call graphs, test coverage, past incident patterns)
instead of asking a model to review a diff in isolation with no context.

**Status: in progress.** This is being built incrementally and documented
honestly as it goes — see [Current status](#current-status) below for what's
actually working today versus what's still ahead.

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
| Retrieval layer (embeddings, call graph, incident tags) | ⏳ Not started |
| LLM risk assessment (Ollama + Gemini fallback) | ⏳ Not started |
| GitHub comment posting | ⏳ Not started |
| Evaluation against labeled historical PRs | ⏳ Not started |

Every phase so far has real tests behind it — duplicate webhook delivery,
concurrent PRs, debounce/supersede, and function-level diff parsing are all
verified against a running Redis instance and real historical GitHub PRs
(not just mocks), including PRs in `psf/requests`, `gin-gonic/gin`, and
`google/gson`.

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

# 5. Run the tests
uv run pytest -v

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
verified against real historical PRs pulled live from GitHub, with ground
truth independently checked by reading the actual patch text before writing
assertions — not just trusting the tool's own first output.
