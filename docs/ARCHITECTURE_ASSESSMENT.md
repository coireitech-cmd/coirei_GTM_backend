# Architecture assessment: market intelligence engine

Date: 2026-10-20. Branch `feat/market-engine`, from `main` at `f08e89f`.

Scope: what in this repository (gtm-crawler) and in the external prototype
[competitor_analy2](https://github.com/Vignesh-web-tech/competitor_analy2)
can carry industry- and product-level market research, and what had to be
built. Each decision says whether it rests on code inspected (I), tests run
(T) or behaviour inferred (B).

## Baseline

| Check | Result |
|---|---|
| `pytest` without PostgreSQL (T) | 775 passed, 178 skipped (DB tests skip) |
| `pytest` with PostgreSQL 16 (`docker compose up -d`) (T) | 944 passed, 9 skipped |
| `ruff check .`, `ruff format --check .`, `mypy` (strict) (T) | clean |
| competitor_analy2 (I) | no tests (`test_ocr.py` is a manual script); not run |

## How data flows today (I)

`gtm-crawl <url>` / `POST /v1/crawls` -> `pipeline.run_job` -> `crawl.crawl`
(robots.txt, sitemaps, durable frontier, `HttpFetcher` behind `NetGuard`,
throttle, browser fallback, raw store) -> bounded external phase (Exa search
for open fields, single pages, no link following) -> `extract/*` (rule-based
company, product, traction, competitor observations with quoted evidence) ->
`assess.py` (per-field states, corroboration, conflicts) -> `dossier.py`
(cited JSON). Competitors are named only on explicit evidence, resolved to a
domain only from a link on the naming page, then crawled (`competitor_max_pages`).

The pipeline is company-centric: every table hangs off `organization` and a
`crawl`, and every extracted field is a company or product attribute. There
is no notion of a market, an industry, a research question, a market-size
figure, or a sizing assumption, and no LLM anywhere.

## gtm-crawler components

| Component | Files | Decision | Evidence and reasoning |
|---|---|---|---|
| Search adapter | `search.py` (`SearchProvider`, `ExaSearch`, `FailoverSearch`, `rank_candidates`) | **KEEP** | Retries, Retry-After, circuit breaker, URL normalization, key never logged (I, `test_search.py` T). Reused unchanged; the market engine depends only on its `search()` shape, so a fake replaces it in tests. |
| Guarded fetching | `fetch/http.py`, `netguard.py` | **KEEP** | Every hop's address checked (SSRF, metadata endpoints, ports), size and decompression limits, typed errors (I; `test_netguard.py`, `test_secure_network.py`, `test_security_phase16.py` T). Reused with per-run tighter timeouts and body cap. |
| Robots and policy | `robots.py`, `policy.py` | **KEEP** | RFC 9309 behaviour incl. unreachable = block (I, T). Market collection calls `PolicyEngine.check(external=True)`. |
| Throttle | `throttle.py` | **KEEP** | Per-host slots and adaptive slow-down (I, T). |
| Parsing | `parse/` (`parse_page`, regions, `main_text`, `published_at`, fingerprints) | **KEEP** | Boilerplate regions, publication date, text hash (I, T). Market extraction reads `blocks` outside nav/header/footer/aside. |
| Quotes and source tiers | `evidence.py` (`quote_block`, `resolve`, `classify_source`) | **KEEP** | Quotes re-derivable from the raw page (I, T). Market evidence uses the same XPath+offset locators; `classify_source` is extended (not changed) by `market.assessment.classify` for government, statistics and market-research publishers. |
| Raw store | `rawstore.py` | **KEEP** | Content-addressed, hash-verified (I, T). Market sources store `raw_sha256`. |
| Site crawl | `crawl.py` | **KEEP** (not reused for open-web market pages) | Built around one site, a DB-backed frontier and a `crawl` row; reusing it for a list of unrelated open-web pages would mean fake organizations and crawls (B). Its external phase is the model for market collection (single pages, no link following). Change: the market config section is excluded from the crawl config snapshot. |
| Extractors | `extract/*` | **KEEP** (not reused for market claims) | They emit company/product fields (`price`, `competitor`, ...) tied to an organization (I). Market claims (sizes, CAGR, drivers, barriers) are about a market, so a separate rule set was written. |
| Field assessment | `assess.py` | **KEEP** (not reused) | States per organization field (I). Market claims need a different unit: a metric per (kind, unit, year) across publishers. Its principles were carried over: independent sources, both sides of a conflict kept, first-party self-interest. |
| Dossier | `dossier.py` (`build_dossier`) | **KEEP**, reused | Product research with `company_id` turns the crawled company's and competitors' cited facts into evidence (`market.engine.dossier_evidence`); claims (UNSUPPORTED) are excluded (T). This is the competitor-analysis reuse: no second crawler. |
| Jobs and worker | `jobs.py`, `worker.py` | **REFACTOR** (small) | The lease/heartbeat/hand-back pattern is right (I, T). The worker now claims a market run when no crawl job waits; crawl jobs keep priority. A separate `market_research` table avoids changing `crawl_job` semantics. |
| API | `api.py` | **REFACTOR** (small) | Auth, rate limit, security headers apply to new routes because they are mounted inside `create_app` (T: `test_token_guards_market_routes`). Routes live in `market/api.py`. |
| Config | `config.py` | **REFACTOR** (small) | New `[market]` section (`MarketSettings`); secrets wrapped and redacted like the rest; LLM URL must be https or localhost. Excluded from crawl config hashes (`versions.NOT_OUTPUT`). |
| Database | `db/models.py`, migration `0016` | **NEW** table | `market_research`: request, status, lease, progress, report JSON. Additive only; drift test passes (T). |
| LLM | none | **NEW**, optional | See below. |

## competitor_analy2 components

| Component | Decision | Evidence and reasoning |
|---|---|---|
| `scraper.py` (Playwright BFS crawl) | **REBUILD** (not used) | No SSRF guard (user URLs reach `localhost`, `169.254.169.254`), no robots.txt, no throttle, subdomains dropped, query strings dropped, whole-page text incl. nav (I). gtm-crawler's fetch stack already does this correctly. |
| `ocr.py` (Tesseract over page images) | **REFACTOR**, ported to `market/ocr.py` | Useful: market figures often live in infographics. Its image heuristics and cleaning are kept; downloads now go through the guarded fetcher and robots.txt, images are size- and pixel-limited, OCR evidence gets an `image` locator and lower certainty. Optional extra `[ocr]`; off by default. Not live-tested here (Tesseract not installed; a fake reader tests the path). |
| `rag.py` (Chroma chunks + embeddings) | **REBUILD** as `market/llm.retrieve` | A vector DB plus model download for a few hundred excerpts is heavy and non-deterministic. TF-IDF over the run's own evidence is stdlib, deterministic and testable; every hit is an evidence record with provenance. |
| `analyzer.py` (LLM answer from top chunks) | **REFACTOR**, ported to `market/llm.ChatModel` | Kept: one OpenAI-compatible client covers OpenAI, Groq, Gemini, Ollama and Anthropic's compatibility endpoint. Added: evidence passed as tagged untrusted data, every sentence must cite known evidence ids (others dropped, none = rejected), output labelled model-generated, never used for numbers or scores. |
| `cache.py` (JSON file cache, TTL) | **REBUILD** (not needed) | Non-atomic writes, no locking across processes (I). Raw pages are in the raw store; earlier evidence is reused from `market_research` reports. |
| `app.py`, `statics/index.html` | not carried over | `STATIC_DIR = "static"` vs folder `statics/` breaks `GET /` (I); no auth. gtm-crawler's API is JSON-only by design (stored-XSS hardening, `docs/security-review.md`). |

## What was missing (NEW)

All under `gtm_crawler/market/` (details: `docs/MARKET_ENGINE_IMPLEMENTATION.md`):
validated models, research planning, open-web single-page collection,
market-claim extraction (sizes, forecasts, CAGR, prices, shares, 17
dimensions), source quality and claim confidence, contradiction detection,
industry and product analysis, TAM/SAM/SOM, opportunity scoring, Markdown
export, run store, worker integration, API, CLI.

## Decisions taken without asking (material assumptions)

1. **Where:** in gtm-crawler, not competitor_analy2: it is the user's repo
   and already has the safeguards; the earlier recommendation (option b).
2. **No open-web crawling:** market pages are fetched one at a time from
   search results (crawl depth 0), consistent with the approved
   external-domain policy (2026-09-29). Competitors' own sites: known
   competitor home and `/pricing` pages, or the crawler via `company_id`.
3. **No LLM by default:** extraction and analysis are rules. A model is an
   optional narrator and Q&A writer only.
4. **Persistence:** one table with the report as JSON, not normalized
   evidence tables: reports are immutable once finished, read whole, and
   bounded by the source budget (B). Revisit if cross-run evidence queries
   are needed.

## Prioritized plan (as executed)

1. Models, extraction, assessment (shared foundation).
2. Industry vertical slice with fixtures; then product slice reusing the dossier.
3. Sizing, then scoring.
4. Store, worker, API, CLI.
5. OCR and model features from competitor_analy2; hardening; docs.

Status per task: `docs/MARKET_ENGINE_IMPLEMENTATION.md`.
