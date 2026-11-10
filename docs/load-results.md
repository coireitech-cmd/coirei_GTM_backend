# Load test results (Phase 16)

Spec sections 29 (load), 32 ("measure before optimizing"), 35 (acceptance).
Every number below was measured on the machine described here by
`python -m gtm_crawler.loadtest <scenario> --out ...` (see README, "Load
testing"), one scenario at a time, nothing else running. Re-run the harness
to reproduce; numbers move by a few percent between runs.

## Machine

| | |
|---|---|
| Platform | Windows 11 (`Windows-10-10.0.26200-SP0`), AMD64 Family 25 Model 116 (AMD) |
| Logical CPUs / RAM | 16 / 15.3 GB |
| Python | 3.11.9 |
| PostgreSQL | 16.15 in Docker Desktop (Linux VM), reached over 127.0.0.1 |

## What is simulated, and what is not

Real code under test: `run_worker` / `run_pipeline`, the crawl loop, frontier,
throttle, robots, sitemaps, raw store (local filesystem), parsing, extraction,
assessment, dossier, the job queue, the HTTP API (FastAPI `TestClient`), the
search failover and circuit breaker, the browser budget/semaphore/breaker, and
PostgreSQL itself.

Simulated:

* **Network**: generated sites behind `httpx.MockTransport`, 2-40 ms latency
  plus jitter, injected HTTP 500s. No TCP, TLS, DNS, slow-loris servers,
  bandwidth limits or real-world HTML variety. Fetch latencies below are
  this simulated latency plus the client's overhead, **not** Internet numbers.
* **Chromium**: `BrowserFetcher._render` replaced by a 50 ms timed stub. The
  real render cost (hundreds of ms to seconds, 100-300 MB per browser) is
  not measured here; only that the budget, concurrency limit and metrics
  around it hold.
* **Search**: an Exa-compatible stub at `api.exa.ai` and a second ("backup")
  Exa-protocol endpoint. There is only one real provider today; the backup
  exists so `FailoverSearch` has somewhere to fail over to.
* **Workers**: the "several workers" are concurrent `run_worker` loops in **one
  process** (one GIL), except `crash_recovery`, whose doomed worker is a real
  child process that is killed (`TerminateProcess`). Multi-process scaling was
  not measured.
* **Memory**: psutil is not installed, so peak memory is the process's peak
  working set from the OS (Win32 `GetProcessMemoryInfo` through `ctypes`;
  `getrusage` on POSIX), not tracemalloc. It covers the harness process only
  (not PostgreSQL, not the killed child).
* **DB statements**: counted by the `db_query_duration_seconds` histogram on the
  scenario's engines. "DB seconds" is summed over concurrent threads, so it can
  exceed wall time.

Settings are production defaults except: `min_delay_seconds` 0 (deep, browser,
provider, db, queue, crash) or 0.05 s (many_domain), `fetch.max_retries` 0 (the
crawl's own retry/DLQ still applies), `worker.lease_seconds` 3,
`crawl.retry_delay_seconds` 0.2, competitor crawls off. These shorten the run,
not the work per page.

## Results (full scale)

| Scenario | Scale | Wall s | Pages | Pages/s | CPU cores busy | Peak MB | DB stmts | DB mean ms | Fetch p50/p90/p99 ms | Loop lag p99/max ms | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deep_single_domain | 3000-page site + trap, then 300-page budget run | 164.4 | 3300 | 20.1 | 0.84 | 119.6 | 44 324 | 6.3 | 2 / 5 / 9 | 27.8 / 79.6 | PASS |
| many_domain | 50 companies x 25 pages, 4 workers, search on | 66.5 | 1400 | 21.1 | 1.11 | 113.1 | 24 793 | 12.7 | 19 / 34 / 45 | 29.3 / 79.9 | PASS |
| browser_heavy | 444-page site, ~90% JS shells, max_renders 300 | 23.3 | 444 | 19.0 | 0.88 | 106.0 | 6 614 | 5.3 | 2 / 5 / 10 | 25.7 / 47.2 | PASS |
| provider_degradation | 2 x 10 companies, 2 workers | 20.9 | 190 | 9.1 | 0.51 | 107.8 | 4 331 | 3.3 | 3 / 14 / 25 | 23.5 / 46.9 | PASS |
| db_pressure | 20 companies, 4 workers, 2-conn pool | 28.2 | 402 | 14.2 | 0.61 | 108.8 | 6 714 | 4.1 | 7 / 16 / 24 | 16.6 / 58.2 | PASS |
| queue_pressure | 40 jobs via API, max_queued_jobs 10, 2 workers | 15.8 | 200 | 12.6 | 0.86 | 120.9 | 5 983 | 3.3 | 2 / 7 / 14 | 33.2 / 428.6 | PASS |
| crash_recovery | 1500-page site, worker killed at ~1/3 | 79.0 | 1501 | 18.9 | 0.57 | 111.3 | 14 233 | 5.8 | 4 / 15 / 21 | 25.7 / 87.2 | PASS |

Event-loop lag is sampled every 50 ms. Its p50 is negative (about -2 ms)
because Windows timers fire up to one clock tick (~16 ms) early.

### Per scenario

**deep_single_domain.** Budget 3500 > site size, so the crawler runs out of
real pages and only the endless `/calendar/YYYY/MM` trap is left: 2998 of
3000 pages fetched; **8** calendar pages fetched in total (bound:
`max_urls_per_pattern` 50; the frontier's depth and low-yield limits stopped it
first, `max_depth` rejections 7). 23 injected 500s, 23 retries scheduled, 0
dead-lettered. The crawl itself took 88.0 s; extraction of 3300 pages took
63.5 s wall / 17.8 s CPU (in a worker thread). Second crawl with
`max_pages` 300: 294 fetches, stop reason `max_pages`, status `partial`.
`frontier.max_urls_per_prefix` (500) shaped the site generator: sections hold
at most 400 pages so the prefix cap does not hide pages.

**many_domain.** 50/50 jobs completed, 45.9 jobs/min with 4 in-process workers
(the process is CPU-bound at ~1.1 cores, so more in-process workers would not
help; separate processes might, not measured). Per-host concurrency max 1
(limit 2). Throttle releases on any site host were at least 24.5 ms apart,
and the mean gap was 66.8 ms on the busiest host, against `min_delay` 50 ms
(see the bug below for the 32 ms slack). 250 search calls, all answered.

**browser_heavy.** 300 renders = `max_renders` 300; 96 further shells wanted a
render and were refused (budget binding). Max 2 renders at once
(`browser.concurrency` 2). `render_ms` total 13 800 (mean 46 ms for a 50 ms
stub), and the `browser_render_duration_seconds` histogram counted exactly
300. Browser usage is bounded and measurable.

**provider_degradation.** Phase A: the primary returns 5xx, times out and
answers 429 in rotation, and the backup is healthy. All 10 crawls got their
search results from the backup (5 queries each). The primary got 60 calls
across 10 crawls, 6 per crawl, which is the breaker's bound
(`circuit_failure_threshold` x (`max_retries`+1)). Phase B: both providers
fail. All 10 crawls completed with `search_error` recorded and their own-site
pages and facts intact. 20/20 jobs completed; the breaker opened 30 times.
Search failure does not collapse the crawl.

**db_pressure.** 4 workers sharing a 2-connection pool (`max_overflow` 0),
+1 ms on every statement, and 3 real server-side statement timeouts injected
mid-run (`pg_sleep` past `statement_timeout` 500 ms). Each timeout ended its
job's run as `job.interrupted` (2) or a failed heartbeat (1). The jobs were
reclaimed after lease expiry (2 reclaim events) and all 20 completed. No
deadlock, no lost or duplicated work.

**queue_pressure.** 40 submissions against `api.max_queued_jobs` 10: 51 `429`
answers, all with `Retry-After: 60`; queued jobs never exceeded 10; every
resubmission with the same `Idempotency-Key` returned the same job (40/40);
40 distinct jobs, all completed. The 428 ms loop-lag spike is the API client
(`TestClient` and app start-up) running in a thread of the same process.

**crash_recovery.** The worker process was killed with 502 of 1500 URLs
processed and 6 URLs leased or in flight. After the 3 s lease expired, a
second worker reclaimed the job (`claimed -> reclaimed -> finalizing ->
completed`), finished the crawl, and all 6 leased URLs reached a final state
(0 left open). 1501 pages for 1497 processed URLs: 4 URLs fetched before the
kill were fetched again. The refetches were stored as duplicates of the
originals, so there are no duplicate original pages and no duplicate evidence.
Recovery took 60.9 s from the kill to completion, including the remaining
~1000 pages.

Checks run after every scenario, all passed: no URL left leased or in flight,
no URL stored twice as an original page in a crawl, and no fact cited twice
from two copies of the same URL.

## Bug found and fixed

**Per-host politeness burst after an event-loop stall** (`gtm_crawler/throttle.py`).
`HostThrottle.slot` reserved start times `min_delay` apart and slept until its
reservation. When the event loop stalled for longer than the delay, the
queued requests woke in the same loop iteration and started together. In
`many_domain` two requests to one site arrived 0.1-0.3 ms apart (min_delay
50 ms); a 200 ms stall reproduced it deterministically (gaps 200.8 ms then
**0.0 ms**). Fix: before sleeping, the slot also reserves its actual start at
least `delay` after the previous actual start, so a stall cannot compress the
gaps that follow it. Regression test:
`tests/test_load_phase16.py::test_throttle_respaces_starts_after_an_event_loop_stall`
(fails before the fix, passes after). After the fix, the smallest gap between
releases was 24.5 ms at a 50 ms delay. The remainder is Windows timer
resolution: `time.monotonic` ticks every ~16 ms and asyncio fires timers up to
one tick early. That is bounded at about 32 ms, which is negligible against
the 0.5 s production default.

## Not fixed (reported)

* **Politeness on hosts shared between jobs is per crawl, not global.**
  Each crawl has its own `HostThrottle`. In `many_domain` 4 workers hit the
  shared third-party host (where search results point) 4 at a time
  (`shared_host_max_concurrency` 4), above `per_host_concurrency` 2. The same
  would happen to a real news site or directory crawled for many companies at
  once. A global per-host limit needs state shared across workers (in
  PostgreSQL or a separate service). That is an architecture change and was
  not made.
* **Log noise under DB faults.** When `record_page` fails, `_store_page` puts
  the exception on its fingerprint future, and no twin page may be waiting on
  it. asyncio then logs "Future exception was never retrieved" at garbage
  collection. It is harmless: the crawl is interrupted and resumes as designed.
  `crawl.py` was not changed.

## Bottlenecks

Profiled with `cProfile` in every thread on a 600-page `deep_single_domain`
run:

* **DB round trips dominate.** About 13.4 statements per stored page (44 324 /
  3300) at a mean of 6.3 ms each, measured under concurrency through Docker
  Desktop's VM network. Blocking time in psycopg's `_exec_command`, statement
  preparation and `select` outweighs everything else. The largest callers:
  `record_links`, `claim` (including `_expire_leases`), `record_fetch`,
  `validators`, `record_page`, `set_state`. Throughput is about 20 pages/s per
  process in every scenario, at 0.6-1.1 CPU cores busy.
* **Raw store**: `rawstore.put` (write, fsync, rename) about 18 ms per page in
  the profile, on the Windows filesystem.
* **Parsing is cheap**: 1.0 ms CPU per page (3.27 s CPU for 3300 pages).
  Simhash and lxml together are under 5% of the profile.
* **Extraction already runs off the event loop.** `reextract` runs in
  `asyncio.to_thread`, and wall time is 3-8x its CPU time (63.5 s wall / 17.8 s
  CPU for 3300 pages), with most of it in `load_page` (DB plus raw-store
  read). Event-loop lag stayed at p99 ≤ 33 ms in every scenario. **Moving
  extraction to a process pool is not justified by these numbers.**
  Batching the per-URL writes, or fewer round trips per page, is where the
  time is, if throughput ever matters more than ~20 pages/s per worker
  process.

## Acceptance criteria (spec section 35)

| Criterion | Status in this simulation |
|---|---|
| Load tests pass for target scale | All 7 scenarios PASS at the scales above. No target scale is set in the spec; these are the scales measured. |
| Worker crash does not permanently lose leased URLs | Demonstrated (crash_recovery: 6 of 6 leased URLs finished; db_pressure reclaims). |
| Crawl resumes after restart | Demonstrated (job reclaimed after lease expiry and completed). |
| Traps are bounded | Demonstrated (8 calendar pages, with nothing else left to crawl). |
| Browser usage is bounded and measurable | Demonstrated with a stub browser (budget, concurrency, `render_ms`, histogram). Real Chromium cost not measured. |
| Search-provider failure does not collapse the crawl | Demonstrated (failover, breaker bound, total outage recorded). |

Not demonstrated: behaviour against the real Internet, real Chromium and the
real Exa API; multi-process and multi-host worker scaling; PostgreSQL on
production hardware. The spec says not to claim production readiness before
the acceptance criteria are demonstrated; these results cover the
simulation only.
