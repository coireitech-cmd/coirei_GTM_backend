# Production readiness (Appendix B phase 18)

Spec section 35 (production acceptance criteria) and rule 16 of section 36:
"Do not claim production readiness until acceptance criteria are
demonstrated." This document records what was demonstrated, how, on what
scale, and what has to happen on the production side before and after
go-live.

**Status (2026-10-01):** all 17 criteria PASS, in the default and the
`--full` run, against a simulated web on one machine. That is what rule 16
requires before going live. It says nothing yet about the live web, real
Chromium cost, the real Exa API or several worker hosts ("Target scale"
below); those are measured during the first week, under the conservative
budgets, after the go-live checklist is done.

## How the criteria are demonstrated

```
python -m gtm_crawler.acceptance [--full] [--out report.json]
```

`gtm_crawler/acceptance.py` maps each of the 17 criteria to the tests and
tools that demonstrate it, runs them, and prints the matrix below. Tests run
in one pytest process (results read from its JUnit XML); tools run as
subprocesses:

| Tool | What it does |
|---|---|
| `corpus` | `python -m gtm_crawler.corpus`: every corpus artifact re-parsed and re-extracted, compared with its golden (observations, qualifiers, quotes, locators, versions) |
| `replay` | seeds a scratch database (`gtm_crawler_replay_acceptance`) with two simulated companies crawled to completion, then `gtm-crawl --replay-all --fail-on-change` on it; passes only if it exits 0, replayed both crawls and read every raw document |
| `drill` | `python -m gtm_crawler.drill`: crawl, kill a worker mid-crawl, back up, drop the database and raw store, restore, compare the dossier, replay, resume the killed crawl, verify (docs/disaster-recovery.md) |
| `load:<scenario>` | `python -m gtm_crawler.loadtest <scenario>`: the seven load scenarios of docs/load-results.md |

A criterion is **PASS** only when every piece of its evidence ran and
passed; **FAIL** if anything failed; **NOT DEMONSTRATED** if something was
skipped or could not run (no Chromium, no `pg_dump`) and nothing failed. A
skipped test is never counted as a pass. Default mode runs the load
scenarios at their small (test-suite) scale and the drill at 200 pages per
site; `--full` runs the load scenarios at full scale and the drill at 1000.

## Results

Default run: 2026-10-01T13:22:00 UTC, commit `fb87c1b+uncommitted`, 199.4 s,
pytest 508 tests (508 passed, 0 failed, 0 skipped).

Full run (`--full`): 2026-10-01T13:18:40 UTC, commit `fb87c1b+uncommitted`,
623.4 s, pytest 508 tests (508 passed, 0 failed, 0 skipped).

`+uncommitted`: this phase's working tree on top of `fb87c1b`, i.e. the code
committed together with this document.

Machine: the load-test machine of docs/load-results.md (Windows 11, 16
logical CPUs, 15.3 GB RAM, Python 3.11.9, PostgreSQL 16 in Docker Desktop).

| # | Criterion | Tests | Tools | Default | Full |
|---|---|---:|---|---|---|
| 1 | Worker crash does not permanently lose leased URLs | 5 | `load:crash_recovery`, `load:db_pressure` | **PASS** (21 s) | **PASS** (100 s) |
| 2 | Crawl resumes after restart | 6 | `load:crash_recovery`, `drill` | **PASS** (50 s) | **PASS** (200 s) |
| 3 | Private/internal destinations cannot be reached | 142 | - | **PASS** (7 s) | **PASS** (7 s) |
| 4 | Redirects are revalidated | 18 | - | **PASS** (1 s) | **PASS** (1 s) |
| 5 | Robots decisions are enforced and auditable | 24 | - | **PASS** (2 s) | **PASS** (2 s) |
| 6 | Traps are bounded | 18 | `load:deep_single_domain` | **PASS** (11 s) | **PASS** (148 s) |
| 7 | Duplicates are controlled | 37 | `load:crash_recovery`, `load:many_domain` | **PASS** (23 s) | **PASS** (139 s) |
| 8 | Browser usage is bounded and measurable | 22 | `load:browser_heavy` | **PASS** (28 s) | **PASS** (46 s) |
| 9 | Search-provider failure does not collapse the crawl | 10 | `load:provider_degradation` | **PASS** (10 s) | **PASS** (25 s) |
| 10 | Every factual field has evidence or is UNKNOWN | 87 | `corpus` | **PASS** (10 s) | **PASS** (10 s) |
| 11 | Conflicts and qualifiers are preserved | 30 | `corpus` | **PASS** (2 s) | **PASS** (2 s) |
| 12 | Extraction is replayable | 9 | `replay`, `drill` | **PASS** (47 s) | **PASS** (160 s) |
| 13 | Versions are reproducible | 38 | `corpus`, `replay` | **PASS** (21 s) | **PASS** (46 s) |
| 14 | DLQ items are inspectable/replayable | 17 | - | **PASS** (1 s) | **PASS** (1 s) |
| 15 | Metrics expose quality and cost | 20 | - | **PASS** (5 s) | **PASS** (4 s) |
| 16 | Backups can be restored | 4 | `drill` | **PASS** (40 s) | **PASS** (129 s) |
| 17 | Load and security tests pass for target scale | 156 | `load:deep_single_domain`, `load:many_domain`, `load:browser_heavy`, `load:provider_degradation`, `load:db_pressure`, `load:queue_pressure`, `load:crash_recovery` | **PASS** (69 s) | **PASS** (383 s) |

Tests = test cases run for the criterion (each case of a parametrized test
counts); the node ids are `CRITERIA` in `gtm_crawler/acceptance.py`. Seconds
are the sum of the evidence's durations; a tool shared between criteria runs
once and counts in each. Nothing was skipped: Chromium and `pg_dump` (through
`docker exec scraper10-postgres-1`) were available.

**The first `--full` run failed** (2026-10-01 13:01 UTC, 15 PASS / 2 FAIL):
`load:many_domain` released two requests to one host 6.8 ms apart at a 50 ms
minimum delay, failing "Duplicates are controlled" and "Load and security
tests pass for target scale". That was a politeness bug, fixed below
("Bugs found by this phase"); the tables are the runs after the fix.

### Tool results

Load scales: default = the test suite's small scale, full = docs/load-results.md.
Peak MB is the harness process only (not PostgreSQL).

| Tool | Default | Full |
|---|---|---|
| `load:crash_recovery` | PASS, 10.4 s: 8/8 checks, scale 60: 60 pages in 7.22 s (8.3 pages/s), peak 102.9 MB | PASS, 71.76 s: 8/8 checks, scale 1500: 1500 pages in 68.01 s (22.1 pages/s), peak 111.7 MB |
| `load:db_pressure` | PASS, 10.41 s: 7/7 checks, scale 3: 60 pages in 7.25 s (8.3 pages/s), peak 104.8 MB | PASS, 27.74 s: 7/7 checks, scale 20: 400 pages in 24.89 s (16.1 pages/s), peak 108.0 MB |
| `drill` | PASS, 30.1 s: 10/10 checks, 200 pages/site: backup 2.294 s, restore 2.509 s, resume 6.65 s | PASS, 119.53 s: 10/10 checks, 1000 pages/site: backup 7.368 s, restore 11.514 s, resume 33.6 s |
| `load:deep_single_domain` | PASS, 8.64 s: 7/7 checks, scale 60: 74 pages in 6.02 s (12.3 pages/s), peak 104.0 MB | PASS, 145.58 s: 7/7 checks, scale 3000: 3302 pages in 140.21 s (23.5 pages/s), peak 120.7 MB |
| `load:many_domain` | PASS, 9.41 s: 8/8 checks, scale 4: 112 pages in 6.13 s (18.3 pages/s), peak 107.8 MB | PASS, 63.5 s: 8/8 checks, scale 50: 1400 pages in 59.33 s (23.6 pages/s), peak 110.8 MB |
| `load:browser_heavy` | PASS, 5.44 s: 8/8 checks, scale 20: 30 pages in 2.35 s (12.7 pages/s), peak 102.7 MB | PASS, 24.07 s: 8/8 checks, scale 300: 449 pages in 20.53 s (21.9 pages/s), peak 106.7 MB |
| `load:provider_degradation` | PASS, 8.26 s: 10/10 checks, scale 2: 38 pages in 5.23 s (7.3 pages/s), peak 104.6 MB | PASS, 22.84 s: 10/10 checks, scale 10: 190 pages in 20.09 s (9.5 pages/s), peak 107.7 MB |
| `corpus` | PASS, 1.34 s: 25 corpus cases match their goldens | PASS, 1.2 s: 25 corpus cases match their goldens |
| `replay` | PASS, 10.51 s: 2 seeded crawls, exit 0: {"crawls": 2, "failed": 0, "changed_crawls": 0, "added": 0, "removed": 0, "changed": 0, "field_state_transitions": 0, "unreadable_pages": 0, "rejected_drafts": 0} | PASS, 34.87 s: 2 seeded crawls, exit 0: {"crawls": 2, "failed": 0, "changed_crawls": 0, "added": 0, "removed": 0, "changed": 0, "field_state_transitions": 0, "unreadable_pages": 0, "rejected_drafts": 0} |
| `load:queue_pressure` | PASS, 7.85 s: 8/8 checks, scale 8: 40 pages in 4.54 s (8.8 pages/s), peak 118.0 MB | PASS, 19.02 s: 8/8 checks, scale 40: 200 pages in 15.55 s (12.9 pages/s), peak 121.5 MB |


## Target scale: what was demonstrated and what was not

The spec sets no numeric target scale. What "pass for target scale" means
here is: every load scenario passes at the scale measured in
docs/load-results.md, on one machine, against a simulated web.

Demonstrated (numbers: docs/load-results.md, docs/disaster-recovery.md and
the `--full` run above):

- About 20 pages/s per worker process on the load-test machine (Windows 11,
  16 logical CPUs, PostgreSQL 16 in Docker Desktop), bound by database round
  trips (about 13 statements per stored page), at 0.6-1.1 CPU cores.
- 50 companies x 25 pages with 4 in-process workers; a 3000-page site plus an
  endless calendar trap; 300 renders against a 300-render budget; the search
  primary failing (5xx, timeouts, 429) and both providers down; a
  2-connection pool with injected statement timeouts; 40 jobs against a queue
  limit of 10; a worker process killed mid-crawl on a 1500-page site.
- Backup and restore at 1000 pages per site (databases of tens of MB, a few
  thousand raw documents), with the killed crawl resuming afterwards and no
  leased URL lost.
- Every adversarial security test of docs/security-review.md, against the
  real network guard, real local sockets and a real Chromium.

Not demonstrated:

- **The real Internet**: real DNS, TLS, slow or hostile servers, bandwidth,
  real HTML variety. All sites are `httpx.MockTransport` fakes; fetch
  latencies are simulated.
- **The real Exa API**: search is an Exa-compatible stub; real quotas,
  latency, result quality and cost per query are unmeasured.
- **Real Chromium cost under load**: the load runs replace the render with a
  50 ms stub; real renders cost hundreds of ms to seconds and 100-300 MB
  each. (Chromium itself runs in the browser and security tests.)
- **Multi-process and multi-host workers**: the "several workers" are loops in
  one process, apart from the killed child in `crash_recovery`. Horizontal
  scaling and the shared-host politeness problem below are unmeasured.
- **Production data volume and hardware**: databases of tens of MB, raw stores
  of thousands of documents. pg_dump/pg_restore and verify time grow with
  size; the RTO has to be re-measured on a real backup.
- **Managed PostgreSQL PITR and S3 versioning restores**: provider features,
  documented in docs/disaster-recovery.md, not exercised.

So the acceptance criteria are demonstrated **for the simulation**. That is
the precondition rule 16 sets for going live; it is not evidence about
behaviour on the live web. The first week below is where that evidence is
collected, under conservative budgets.

Container hardening done in parallel to this phase (Chromium OS sandbox,
worker memory limits, a DNS-leak test for the browser, a fix for an
unretrieved future exception in `crawl.py`) is recorded in
docs/security-review.md, not here.

## Go-live checklist

For whoever runs the production backend. Tick each item before the first
production crawl.

**Secrets and identity** (environment only, from the secret manager; never in
a config file, image or repository):

- [ ] `GTM__IDENTITY__USER_AGENT_CONTACT`: a URL or mailbox a site operator can
      actually reach. It is sent with every request; the prod profile refuses
      to start without it.
- [ ] `GTM__API__TOKEN`: a long random token (prod refuses to start without
      it); rotation in docs/staging.md (`GTM__API__PREVIOUS_TOKEN`).
- [ ] `GTM__SEARCH__API_KEY`: the Exa key (search is off while it is empty).
      Set a spending cap with the provider.
- [ ] `GTM__DATABASE__URL`: a least-privilege role for the API and workers;
      migrations may use an owner role.

**Data stores**

- [ ] Managed PostgreSQL (16 or newer) with automated backups and
      point-in-time recovery (RPO <= 15 min, docs/disaster-recovery.md),
      encryption at rest, private network only.
- [ ] Raw store on S3 (`rawstore.backend = "s3"`) with **bucket versioning**,
      a lifecycle rule that expires noncurrent versions after the database
      backup retention (e.g. 35 days), cross-region replication if site loss
      matters, public access blocked, encryption on.
- [ ] `gtm-crawl --prune` scheduled (90-day retention, approved), never while
      a backup runs.

**Configuration and deployment**

- [ ] `GTM_ENV=prod` with `deploy/config.prod.toml` mounted as
      `config.prod.toml`, unchanged. Its budgets are the conservative initial
      ones: 200 pages, 30 min and 100 MiB per crawl, concurrency 4, per-host
      concurrency 1, 1 s delay, 20 renders, 5 search queries, 2 competitor
      crawls, 50 queued jobs.
- [ ] `fetch.allowed_networks` stays empty.
- [ ] Migrations: `alembic upgrade head` as a one-off job before the api and
      workers of each release (the compose `migrate` service pattern).
- [ ] Probes: api liveness `/livez`, readiness `/readyz` (gates traffic,
      including during rolling deploys); worker liveness = its metrics port
      (`worker.metrics_port`, default 9100) answering.
- [ ] Worker stop grace period of at least 60 s (SIGTERM hands the job back).
- [ ] Browser workers run with the container hardening recorded in
      docs/security-review.md (sandbox, memory limit).

**Observability**

- [ ] Prometheus scrapes the workers (job `gtm-worker`) and the API
      `/metrics` (job `gtm-api`) and loads `deploy/prometheus/alerts.yml`;
      every alert is routed to someone (page vs. warn).
- [ ] Grafana with `deploy/grafana/crawl-health.json` imported.
- [ ] Logs (JSON lines on stderr) shipped and retained. They are redacted but
      contain crawled URLs.
- [ ] `/metrics` and the probes are unauthenticated by design: keep them on
      the internal network, not on the public entry point.

**Backups and drills**

- [ ] Nightly `python -m gtm_crawler.backup backup` if PITR is not
      available, with an alert on a night without `manifest.json`.
- [ ] A first restore drill before go-live (restore into a scratch database,
      `gtm-crawl --verify`, `--replay-all --fail-on-change`), then monthly and
      after every PostgreSQL major upgrade; record the restore time against
      the 1 h RTO.

**Release gates**

- [ ] `python -m gtm_crawler.acceptance` passes on the release commit
      (`--full` before the first release and after architecture changes).
- [ ] `deploy/canary-gate.sh <prod ref>` for every release that changes
      parsing, extraction or assessment or adds a migration; then one canary
      worker, then the rest (docs/staging.md).
- [ ] The smoke test (`python -m gtm_crawler.smoke`) against the deployment
      after every deploy, on a site you are allowed to crawl.

**Edge and consumers**

- [ ] A reverse proxy or API gateway in front of the API with TLS, a
      per-client rate limit and a request-size limit. The API has one bearer
      token and queue backpressure (`api.max_queued_jobs`), but **no
      per-client rate limit or quota** (docs/security-review.md, gap 5).
- [ ] Every downstream UI HTML-escapes dossier text, quotes and URLs. Crawled
      text is stored and served verbatim on purpose (evidence must match the
      source), so inserting it as HTML is stored XSS (security review, gap 6).
- [ ] A takedown path: a site operator who writes to the user-agent contact
      can be stopped from being crawled (see open item 4).

## First-week ramp plan

**Day 0.** Deploy with `deploy/config.prod.toml` unchanged, 1 worker, the Exa
spending cap set. Run the smoke test against a site you own or may crawl.
Submit 5-10 companies by hand and read each dossier with its lineage, quality
report (`GET /v1/crawls/{id}/quality`) and cost (`GET /v1/companies/{id}/cost`): are the UNKNOWNs right, is every
cited quote on its page, is anything attributed to the wrong company?

**Days 1-3.** Up to about 50 companies a day on 1-2 workers. Daily, on the
crawl-health dashboard: HTTP error ratio, policy blocks and robots refusals,
fetch latency p90, browser renders by outcome and browser memory, search
calls by outcome, circuit-breaker trips, open dead-letter items, duplicate
ratio and unique-content yield, fields left open per job, cost per job. Every
alert gets an owner and a note. Inspect each dead-letter item
(`GET /v1/dead-letters`) before requeueing it. Check politeness in the fetch
logs, including third-party hosts shared between jobs (open item 1), and
answer every operator complaint the same day.

**Days 4-7.** Raise one budget at a time, only where a measurement shows the
budget (not the site) is the limit and the dashboards show headroom:
`crawl.max_pages` when many crawls end `partial` with `stop_reason=max_pages`
while yield stays high; `browser.max_renders` when JS-heavy sites are refused
renders and browser memory is well below its limit;
`search.max_queries_per_crawl` when fields stay open and the Exa cost per job
allows it. Record each change and why, and keep staging's config identical
(a test enforces it). Add workers only when jobs wait in the queue and the
database latency p99 stays flat: database round trips are the measured
bottleneck. End of the week: a restore drill on the real backup,
`--replay-all --fail-on-change` on a sample, and a written review of quality,
cost and incidents before the next increase.

**Roll back** (code: the previous image; budgets: the previous config) on
dead-letter growth that does not stop, a repeating page alert, cost per job
above the agreed cap, or any fact found without supporting evidence. The last
one is a correctness incident: stop extraction rollouts and investigate.

## Bugs found by this phase, and fixed

1. **Per-host politeness after a late wake-up** (`gtm_crawler/throttle.py`).
   The first `--full` run failed `load:many_domain`: two requests to one site
   were released **6.8 ms** apart at a 50 ms minimum delay (check: at least
   delay minus 32 ms of Windows timer slack). Phase 16 had fixed a burst after
   an event-loop stall by reserving each request's start against the
   previous *reserved* start. But a request whose timer fired early slept
   again until its reservation, and if the loop stalled then, it was released
   late while the next request kept its own reservation, so the gap after it
   shrank by the length of the stall. On a busy machine that happens (this run
   shared the machine with other test workloads; Phase 16's run did not). Fix: a request
   is released only `delay` after the previous request's *actual* release,
   recorded at the moment of release; check and record have no `await`
   between them, so concurrent waiters cannot both pass. Regression test:
   `tests/test_acceptance_phase18.py::test_throttle_spacing_survives_a_late_wakeup`
   (gap 31 ms at a 100 ms delay before the fix; at least 95 ms after).
   `tests/test_policy.py::test_sitemap_requests_obey_robots_and_the_throttle`
   now gives the throttle a fake clock that its fake sleep advances (the
   throttle re-checks its clock after sleeping; a no-op sleep against the
   real clock made it spin until the real delay had passed). The matrix
   above is the run after the fix.
2. **Configuration hash covered non-output settings** (`gtm_crawler/versions.py`):
   see open item 2.

## Open items needing an owner decision

1. **Cross-job per-host politeness** (docs/load-results.md). Each crawl has
   its own `HostThrottle`, so workers crawling different companies can hit a
   shared third-party host (a news site, a directory or review site that
   search results point to) at the same time. Measured: 4 concurrent requests
   to one shared host with `per_host_concurrency` 2. With prod's
   `per_host_concurrency = 1` and N workers, a shared host can still see up
   to N concurrent requests. A global per-host limit needs state shared
   across workers (PostgreSQL rows or advisory locks, or a separate service):
   an architecture change that needs approval. Until then keep the worker
   count low, keep `external.max_search_pages` and `max_reference_pages`
   conservative (prod: 10 and 5), and watch for complaints.
2. **The configuration hash changed definition (fixed in this phase).** The
   `config` version hash included the environment name, the raw-store
   location, logging, prices and the contact address, so staging and
   production with identical budgets, or two hosts with different raw-store
   paths, recorded different versions for the same output. It now covers only
   output-affecting settings (`versions.NOT_OUTPUT`; regression test
   `tests/test_acceptance_phase18.py::test_config_hash_follows_output_affecting_settings_only`).
   Crawls recorded before the change keep their old hash, so replaying them
   reports `config` under `versions.changed` once. That is informational (it
   never sets `changed`), but readers of canary reports should know.
3. **No per-client API rate limit or quota** (security review gap 5): the
   reverse proxy in the checklist is required, unless a per-client limit is
   built into the API.
4. **No deny list.** Apart from robots.txt there is no configured list of
   hosts never to crawl. Decide whether one is needed before customers can
   submit arbitrary companies (an operator opt-out has no place to go today).
5. **Real-web numbers.** Real Chromium render cost, real Exa latency and
   price, and multi-host worker throughput are unmeasured. Decide who reviews
   the first week's numbers and sets the next budgets.
6. **No parse wall-clock limit** (security review gap 3): parsing is bounded
   by size and element caps, not by time. Accept it, or add a time limit
   (which needs process isolation).
7. **Shared test databases.** The drill test used a fixed database name
   (`gtm_crawler_drill_pytest`), so two test runs on one server collided. It
   now honours `GTM_DRILL_DATABASE_URL`. CI that runs jobs in parallel should
   give each its own `GTM_TEST_DATABASE_URL`, `GTM_LOAD_DATABASE_URL` and
   `GTM_DRILL_DATABASE_URL`.
