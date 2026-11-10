# Mapping to the Complete Production Specification

How this repository maps to *GTM Intelligence Crawler — Complete Production
Specification* (the engineering source of truth), and the decisions approved
while implementing it phase by phase (Appendix B).

## Approved decisions (2026-09-29)

| Topic | Decision |
|---|---|
| Schema (§21) | Keep the existing tables under their current names (mapped below). Each missing spec table is added in the phase that writes it. No renames of live data. |
| States (§5, §19) | Job and URL states follow the spec from Phase 1 (`gtm_crawler/domain.py`). Field states follow the spec from Phase 11 (method fa-v2: FOUND → SUPPORTED, VERIFIED → CORROBORATED, UNVERIFIED → UNSUPPORTED, plus STALE; `gtm_crawler/assess.py`). fa-v1 rows keep their states as history. |
| Layout (§4) | Flat `gtm_crawler/` package (mapped below). New subsystems go into spec-named modules as they are built. |
| Configuration (§3, §31) | The existing typed loader, extended (profiles, secrets, feature flags, DB limits, fail-closed prod), instead of adding pydantic-settings. |
| HTTP client (§3) | httpx (the spec's "or equivalent"): the network guard hooks into its transport. |
| Logging (§3) | Own JSON formatter (the spec's "structlog or equivalent"). |
| HTTP/2 (§10) | Opt-in (`fetch.http2`, off by default); `h2` is an optional extra. |
| Rate control (§7) | AIMD per host in the existing throttle; circuit breakers in `breaker.py` (in-process, per crawl); no new dependency. Starts at `per_host_concurrency` and only slows down from there. |
| Artifacts (§22, §25) | New `artifact` table keyed by SHA-256, referenced by `fetch`, `robots_snapshot` and `sitemap_fetch`; rows outlive pruned bodies (`pruned_at`). Evidence roles (supports / qualifies / contradicts / superseded) are stored per assessment in `corroboration` (Phase 11). |
| Main text (§3, §12) | In-house region heuristics (semantic tags, roles, class/id hints) instead of trafilatura: evidence needs the parser's own blocks and XPaths. |

## Tables (§21)

Singular table names are the repository's convention.

| Spec table | Here | Status |
|---|---|---|
| companies | `organization` | exists |
| crawl_jobs | `crawl_job` (+ `crawl`: one row per site crawled by a job) | exists |
| urls | `url` (normalized URLs) + `crawl_url` (frontier state per crawl) | exists |
| pages | `page` | exists |
| sources | source tier/type on `observation` (depends on the subject) | as columns; per-source tier and age also in `field_assessment.confidence_components` (Phase 11) |
| evidence | `evidence` | exists |
| observations | `observation` (+ `extraction` runs, `relationship`) | exists |
| field_states | `field_assessment` | exists |
| corroborations | `corroboration`: each observation's role and source per assessment | Phase 11 (migration 0012) |
| structured_data | `page.structured_data` (JSON-LD) | as a column; Phase 5 |
| links | `link` | exists |
| robots_snapshots | `robots_snapshot` | exists |
| sitemap_fetches | `sitemap_fetch` | Phase 4 |
| crawl_events | `crawl_event` (job transitions) | Phase 1 |
| search_queries, search_results | `search_query`, `search_result` | Phase 9 |
| artifacts | `artifact` (stored document: hash, size, type, format version, pruned) + `fetch` (retrieval event) + raw store | Phase 7 |
| dead_letter_items | `dead_letter_item` | Phase 13 |
| cost_events | `cost_event` | Phase 14 |

## Modules (§4)

| Spec area | Module |
|---|---|
| api | `api.py` |
| application | `pipeline.py`, `jobs.py`, `worker.py`, `cli.py` |
| domain | `domain.py`, `db/models.py` |
| infrastructure | `db/`, `rawstore.py`, `search.py`, `metrics.py`, `log.py` |
| crawler/frontier, scheduler | `frontier.py`, `crawl.py`, `db/store.py` |
| crawler/robots, sitemap | `robots.py`, `sitemaps.py`, `policy.py` (policy engine) |
| crawler/normalization | `urls.py` |
| crawler/traps | `frontier.py` (admission, unique-content yield) |
| crawler/rate_control | `throttle.py` (adaptive), `breaker.py`, `fetch/http.py` (retries, Retry-After) |
| parsing | `parse/` |
| extraction | `extract/` |
| evidence | `evidence.py`, `db/evidence.py` |
| verification | `assess.py` |
| gtmdossier | `dossier.py` |
| security | `netguard.py`, `fetch/`, `config.py` (fail-closed) |
| config | `config.py`, `versions.py` |

## Phases (Appendix B)

| # | Phase | Status |
|---|---|---|
| 1 | Foundation | done |
| 2 | URL system | done: normalization, persistent frontier, explainable priority (incl. freshness, parent topic), structured-data discovery, stored admission decisions |
| 3 | Secure network | done: guard (DNS cache, explicit allowlist, metadata always refused), TTFB/write timeouts, decompression ratio, error taxonomy, conditional requests, opt-in HTTP/2 |
| 4 | Robots + sitemap | done: policy engine (ALLOW/BLOCK/DEFER/REJECT with reasons), refusals audited in crawl_event with the robots snapshot applied, redirect hops checked, robots expiry, sitemap_fetch, throttled sitemap requests |
| 5 | Parser + content hashing | done: main text for div layouts (class/id hints), description, headings, Schema.org microdata, recorded parser limits, crash-proof parsing with raw kept |
| 6 | Crawl intelligence | done: adaptive per-host concurrency and delay, per-host circuit breaker, Crawl-delay cap (DEFER, never shortened), per-host URL budget, traps for session ids, sort/filter combinations and low unique-content yield (template duplication, pagination loops, repeated redirects) |
| 7 | Artifact storage + evidence | done: `artifact` table with FKs and backfill (migration 0009), artifact format version, prune records `pruned_at`, full lineage from field to artifact (`field_lineage`, extended `trace`), dossier citations carry locator, versions and artifact hash (dossier schema 2) |
| 8 | Browser fallback | done: noscript-notice trigger, per-render request and byte budgets, rendered-HTML size limit, V8 heap cap, browser recycling and crash relaunch (bounded), browser circuit breaker, render cost in crawl stats and metrics |
| 9 | Search provider abstraction | done: normalized results, retries with Retry-After, failover across providers with per-provider circuit breaker, per-crawl query budget, `search_query` / `search_result` (migration 0010); second provider awaits approval |
| 10 | Deterministic GTM extraction | done: existing extractors cover identity, products, pricing with qualifiers, traction, funding, partnerships, competitors, press (recent_update), geography; new `extract/signals.py` adds customers, case studies, integrations, job openings, open roles, office locations and explicitly stated website technology |
| 11 | Verification + field states | done: spec field states (method fa-v2; FOUND → SUPPORTED, VERIFIED → CORROBORATED, UNVERIFIED → UNSUPPORTED), STALE for time-varying figures older than `stale_after_days`, stored confidence components, evidence roles in `corroboration` (migration 0012), dossier shows UNSUPPORTED values as claims (`claimed_value` + note), STALE fields are search gaps |
| 12 | GTM dossier + API | done: dossier schema 4 with spec section 20 `sections`, state `summary`, `freshness` (last crawl, oldest/newest evidence, STALE fields) and the latest crawl's `versions`; lineage API `GET /v1/companies/{id}/lineage/{field}` (every item of a list field, traced to the raw artifact); `nosniff` on all responses |
| 13 | Durable queue + DLQ + backpressure | done: retry policy by error class, dead-letter queue (`dead_letter_item`, migration 0011) for URLs and jobs with requeue/discard API, lease-expiry limit, 429 + `Retry-After` past `api.max_queued_jobs`, queue depth and DLQ metrics |
| 14 | Observability + cost accounting | done: stdlib W3C-traceparent tracing (spans as JSON log records, API → worker propagation via the job's `created` event, `[logging] trace_sample_rate`); cost accounting (`cost_event`, migration 0013; `[cost]` unit prices; HTTP/browser/search/storage/database/worker usage per crawl; `GET /v1/companies/{id}/cost`, job result and dossier `cost`, schema 5); crawl-quality report (`quality.py`, `GET /v1/crawls/{id}/quality`, job result `quality`); section 27 metrics; `deploy/prometheus/alerts.yml` and `deploy/grafana/crawl-health.json` (metric names tested against the registry) |
| 15 | Replay system | done: extraction runs record `versions` and `trigger` (crawl/replay, migration 0014); compute split from record (`plan_extraction`); `replay.py` dry-run or write replay of stored crawls without network access, compared with the previous run (observations added/removed/changed, field-state transitions, versions, unreadable pages); `gtm-crawl --replay/--replay-all [--write] [--fail-on-change]`; synthetic artifact corpus (`tests/corpus`, 25 cases) with golden observations, `python -m gtm_crawler.corpus [--update]`, and a version-bump rule for changed output |
| 16 | Load + security testing | done: adversarial security tests (`tests/test_security_phase16.py`, `security` marker; API security headers, docs off, idempotency-key conflicts, IPv6-embedded IPv4 and numeric-host SSRF forms, log-injection escaping, browser userinfo URLs, bad redirects) and `docs/security-review.md` with open gaps; load harness `python -m gtm_crawler.loadtest` (7 scenarios on a simulated web, `load` marker, `GTM_LOAD=1` for full scale), results in `docs/load-results.md`; throttle fix for request bursts after an event-loop stall |
| 17 | Staging + disaster recovery | done: integrity check (`gtm-crawl --verify`: raw documents present with matching SHA-256, lineage of every stated field); backup/restore (`python -m gtm_crawler.backup`, pg_dump + raw-store copy + manifest, restore refuses tampered dumps and non-empty DBs); automated restore drill (`python -m gtm_crawler.drill`) incl. a crawl interrupted mid-run resuming after restore; `docs/disaster-recovery.md` (RPO/RTO, measured times, runbook); fail-closed `staging` profile; conservative `deploy/config.{staging,prod}.toml`; smoke test (`python -m gtm_crawler.smoke`) run against the built compose stack incl. a worker replaced mid-crawl; canary gate `deploy/canary-gate.sh` (additive-migration check, corpus, replay `--fail-on-change`); `/readyz` stays ready when the schema is ahead (rolling deploys); dual API token for rotation; `docs/staging.md` |
| 18 | Production hardening + acceptance | done: Chromium OS sandbox on in the container (`[browser] sandbox`, fail-closed in staging/prod; `deploy/chrome-seccomp.json` = Docker default + clone/unshare, no SYS_ADMIN), measured worker memory limit (2560m), DNS-leak test (`deploy/sandbox-test/`), `_store_page` failed-future fix; throttle spacing on actual release; config hash limited to output-affecting settings; acceptance runner `python -m gtm_crawler.acceptance [--full]` (17/17 section-35 criteria PASS); `docs/production-readiness.md` (go-live checklist, ramp plan, open items) |
