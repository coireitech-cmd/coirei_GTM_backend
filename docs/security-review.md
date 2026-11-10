# Security review (Phase 16)

Scope: the threat model of spec section 28 and the security tests of spec
section 29, checked against the code on `feat/gtm-crawler` at Phase 16.
For each threat: the control (file:function), the tests that exercise it, and
a status:

- **covered**: control and tests existed before Phase 16 (Phase 16 may add
  adversarial cases on top);
- **added in Phase 16**: the control or a part of it was missing or wrong
  and was fixed in this phase (see "Vulnerabilities fixed");
- **gap**: not controlled, or controlled but not tested. Gaps are listed at
  the end.

Adversarial tests live in `tests/test_security_phase16.py` (marker
`security`; `pytest -m security`), abbreviated `sec16` below. They run
without the Internet: sites are `httpx.MockTransport` fakes or local sockets
behind the real network guard; the browser tests start a real Chromium.

## Section 28: threat model

| Threat | Control (file:function) | Tests | Status |
|---|---|---|---|
| SSRF | `urls.py:normalize_url` / `_normalize_host` (http(s) only, no user info, valid host, only dotted-quad IPv4); `netguard.py:NetGuard.resolve` + `ip_policy` + `is_public_ip` (every resolved address must be global unicast, port in `fetch.allowed_ports`); `netguard.py:_GuardedBackend.connect_tcp` (the check sits at the connection layer, so every request and hop goes through it); `policy.py:PolicyEngine.check` (decision before network access) | `test_netguard.py` (`test_is_public_ip`, `test_resolve_policy`, `test_real_transport_blocks_loopback_without_connecting`, `test_leading_zero_ipv4_is_not_a_bypass`), `test_urls.py:test_rejects_uncrawlable_urls`, sec16 `test_hostile_destinations_never_pass_the_guard` (~90 spellings: every non-public class, IPv6 embeddings, numeric IPv4 spellings, IDNA mappings, user-info/parser-differential tricks, other ports and schemes), `test_numeric_ipv4_spellings_are_invalid_whatever_the_resolver`, `test_idna_mapped_ip_literals_are_normalized_to_the_literal`, `test_public_destinations_still_pass` | covered; IPv6 ranges and numeric IPv4 hosts **added in Phase 16** |
| DNS rebinding | `netguard.py:NetGuard.resolve` resolves once and returns the checked addresses; `_GuardedBackend.connect_tcp` connects to that address (TLS still verifies the original hostname); cached answers are re-checked on every connection | `test_netguard.py:test_connection_goes_to_the_checked_address_with_original_host`, `test_secure_network.py:test_dns_answers_are_cached_bounded_and_rechecked`, sec16 `test_dns_rebinding_cannot_swap_the_checked_address`, `test_dns_rebinding_is_refused_on_a_fresh_connection`, `test_mixed_answers_are_refused_whole` | covered |
| Redirect to private network | `fetch/http.py:HttpFetcher._fetch` follows redirects manually: each `Location` is normalized (`normalize_url`), limited (`fetch.max_redirects`), loop-checked, and connected through the guard; `fetch/http.py:classify` maps an unparseable `Location` to `INVALID_REDIRECT` | `test_fetch.py:test_redirect_limits`, `test_netguard.py:test_redirect_to_disallowed_address_is_blocked_at_the_hop`, sec16 `test_redirect_to_a_forbidden_target_is_not_followed` (file/gopher/data/javascript/ftp, credentials, numeric IPv4, broken IPv6 literal), `test_redirect_chain_into_private_and_metadata_addresses_is_blocked_at_the_hop` (real sockets: only the first hop connects), `test_redirect_loops_and_long_chains_stop` | covered; bad-`Location` classification **added in Phase 16** |
| Cloud metadata access | `netguard.py:METADATA_ADDRESSES` + `ip_policy` (refused even inside `fetch.allowed_networks`, and now also in IPv4-mapped/6to4/NAT64/Teredo form); `metadata.google.internal` resolves to 169.254.169.254 and is refused by address | `test_secure_network.py:test_allowlist_is_explicit_and_never_opens_metadata`, sec16 `test_metadata_is_refused_even_inside_allowed_networks`, `test_crawl_of_a_metadata_address_makes_no_connection` (DB, whole crawl) | covered; embedded forms **added in Phase 16** |
| Decompression / oversized payloads | `fetch/http.py:HttpFetcher._read_response` (streamed; transferred and decoded bytes each capped at `fetch.max_body_bytes`; declared `Content-Length` checked first but never trusted as an upper bound; only `gzip`/`deflate`; content type checked before the body is read), `fetch/http.py:LimitedDecoder` (size and ratio limit), `fetch/http.py:gunzip_limited` (sitemaps); `robots.py:RobotsCache._load` (only `robots.max_bytes` parsed; a failed/oversized fetch is `UNREACHABLE` = disallow all) | `test_fetch.py` (`test_declared_oversize_rejected_before_reading`, `test_streamed_oversize_without_content_length`, `test_decompression_bomb_is_stopped`, `test_transfer_size_capped_even_when_decoded_output_is_small`, `test_content_type_filter_skips_body`), `test_secure_network.py:test_decompression_ratio_limit`, sec16 `test_endless_body_without_content_length_is_cut_off`, `test_understated_content_length_does_not_lift_the_limit`, `test_malformed_content_length_is_not_trusted`, `test_compression_bombs_are_stopped`, `test_nested_gzip_is_decoded_once_and_bounded`, `test_unrequested_encodings_are_refused`, `test_wrong_content_types_are_not_read`, `test_content_type_parameters_and_case_do_not_bypass_the_check`, `test_oversized_robots_txt_fails_closed`, `test_gzip_bomb_sitemap_is_refused` | covered |
| Parser abuse | `parse/dom.py:_Walker._emit` (`parse.max_blocks`), `parse/dom.py:_links` (`parse.max_links`), `parse/structured.py:_typed_nodes` (JSON-LD depth cap 64; malformed blocks recorded, not raised), `sitemaps.py:_xml_parser` (`resolve_entities=False`, `no_network=True`, `huge_tree=False`), `sitemaps.py:discover_sitemaps` (`sitemap.max_sitemaps`) | `test_parse.py:test_limits_truncate_and_flag`, `test_parse_phase5.py:test_pathological_nesting_is_bounded_and_reported`, `test_sitemaps.py:test_external_entities_are_not_resolved`, `test_limits`, sec16 `test_huge_attributes_and_many_elements_are_bounded`, `test_json_ld_abuse_is_contained`, `test_billion_laughs_sitemap_is_not_expanded`, `test_sitemap_index_fan_out_is_bounded` | covered (no parse wall-clock limit: see gaps) |
| Browser escape / resource exhaustion | `fetch/browser.py:BrowserFetcher._render` (fresh context per render, service workers blocked, downloads off, WebSockets mocked, wall-clock `render_timeout`), `BrowserFetcher._forward` (every Chromium request intercepted: only document/script/xhr/fetch, http(s) only, no user info, request/byte budgets, robots, throttle, then `HttpFetcher.request_once` through the guard), `_CHROMIUM_ARGS` + dead proxy (anything escaping interception cannot connect; WebRTC no non-proxied UDP), `BrowserFetcher._acquire` (V8 heap cap, recycle, crash relaunch limit), circuit breaker | `test_browser.py:test_browser_cannot_reach_private_addresses`, `test_render_timeout_is_an_error`, `test_browser_phase8.py` (budgets, oversized render, recycle, crash relaunch, breaker), sec16 `test_browser_requests_meet_the_guard_and_cannot_escape` (real Chromium: popups, fetches to internal names and loopback, redirect to loopback, `file://` iframes, credentialed script URL, forced download), `test_browser_route_refuses_before_any_fetch` | covered; user-info refusal **added in Phase 16**; OS sandbox, container memory limit and a DNS-leak test **added in Phase 18** (see "Closed in Phase 18") |
| Queue poisoning | `api.py:CrawlRequest` (types and lengths; empty idempotency key refused), `api.py:create_crawl` (root normalized and guarded like any URL; domain must match; key reuse for another `root_url` is 409), `jobs.py:JobStore.create` (`api.max_queued_jobs` backpressure), `deadletter.py:DeadLetters.requeue` (rebuilds work from stored state, never from the payload), worker DLQ for URLs/jobs that keep failing or killing workers | `test_api.py:test_create_is_idempotent_and_validated`, `test_queue_phase13.py` (`test_failing_and_poison_urls_are_dead_lettered_and_the_crawl_goes_on`, `test_a_url_that_keeps_killing_its_worker_is_dead_lettered`, `test_new_jobs_are_refused_while_the_queue_is_full`), sec16 `test_malformed_job_requests_are_refused_without_side_effects`, `test_idempotency_key_cannot_be_replayed_for_another_target`, `test_dead_letter_payload_is_data_and_requeue_uses_stored_state` | covered; 409 and empty-key refusal **added in Phase 16** |
| Credential leakage | `urls.py:normalize_url` (credentials in URLs rejected, incl. redirect targets), `fetch/browser.py:BrowserFetcher._forward` (user info refused), `config.py:Secret` + `secret_values` (repr `***`), `log.py:JsonFormatter.redact` (configured secrets, `user:pass@`, `token@`, bearer tokens), `api.py:authorized` (`hmac.compare_digest`), `config.py:_check_prod` (prod refuses to start without `api.token`), `Set-Cookie` never stored | `test_log.py:test_secrets_and_credentials_are_redacted`, sec16 `test_credentials_are_redacted_in_every_form`, `test_api_token_is_a_redacted_secret`, `test_redirect_with_credentials_does_not_leak_into_logs`, `test_malformed_job_requests_are_refused_without_side_effects` (no echo of the password) | covered; `token@host` redaction **added in Phase 16** |
| Log injection | `log.py:JsonFormatter.format` (one JSON object per line; JSON escapes C0 controls; NEL, U+2028, U+2029 escaped) | `test_log.py:test_untrusted_content_cannot_forge_log_lines`, sec16 `test_no_character_splits_a_log_record` (every character `str.splitlines` splits on) | covered; NEL **added in Phase 16** |
| SQL injection | All queries are SQLAlchemy Core/ORM with bound parameters; the only f-string SQL (`quality.py:quality_report`) concatenates constant fragments; path/query params are typed by FastAPI | sec16 `test_injection_in_path_and_query_parameters_is_rejected`, `test_crawled_injection_strings_are_stored_intact` (DB) | covered |
| Stored XSS from untrusted content | API returns JSON only; `api.py:SECURITY_HEADERS` on every response (`nosniff`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`); Swagger/ReDoc HTML disabled; crawled text is stored verbatim as data (never sanitized-and-rendered) | sec16 `test_every_response_is_inert_json`, `test_crawled_injection_strings_are_stored_intact`, `test_dead_letter_payload_is_data_and_requeue_uses_stored_state` | covered; CSP/frame headers and HTML docs removal **added in Phase 16** |
| DoS via crawl traps | `frontier.py:Frontier._admission_check` (length, depth, query params, repeated segments, per-shape and per-prefix caps, session ids, query variants per path, `max_urls_per_host`), `Frontier.record_yield`/`low_yield` (low unique-content yield closes a shape), crawl budgets in `crawl.py`, `sitemaps.py:discover_sitemaps` fan-out cap | `test_frontier.py` (`test_trap_rules`, `test_pattern_budget_caps_calendar_and_facet_traps`, `test_prefix_budget_limits_one_section`), `test_crawl_intelligence.py`, sec16 `test_trap_flood_admits_a_bounded_number_of_urls` (17k+ trap URLs, <= 200 admitted), `test_sitemap_index_fan_out_is_bounded` | covered |

## Section 29: security tests

| Required test | Where | Status |
|---|---|---|
| Private IPs | `test_netguard.py:test_is_public_ip`, sec16 `test_hostile_destinations_never_pass_the_guard`, `test_metadata_is_refused_even_inside_allowed_networks`, `test_public_destinations_still_pass` (no over-blocking) | covered; added in Phase 16 |
| Redirect validation | `test_fetch.py:test_redirect_limits`, `test_netguard.py:test_redirect_to_disallowed_address_is_blocked_at_the_hop`, sec16 redirect tests (see above) | covered; added in Phase 16 |
| DNS rebinding | `test_netguard.py:test_connection_goes_to_the_checked_address_with_original_host`, `test_secure_network.py:test_dns_answers_are_cached_bounded_and_rechecked`, sec16 rebinding tests | covered |
| Payload limits | `test_fetch.py` size/bomb tests, `test_secure_network.py:test_decompression_ratio_limit`, sec16 payload and parser tests | covered; added in Phase 16 |
| Browser isolation | `test_browser.py:test_browser_cannot_reach_private_addresses`, `test_browser_phase8.py`, sec16 `test_browser_requests_meet_the_guard_and_cannot_escape`, `test_browser_route_refuses_before_any_fetch`; in the built image: `deploy/sandbox-test/run.sh` (sandbox, DNS, memory limit) | covered (needs Chromium: `playwright install chromium`; the container check needs Docker) |
| Secret redaction | `test_log.py`, sec16 credential tests | covered; added in Phase 16 |

## Vulnerabilities fixed in Phase 16

Each was found by a Phase 16 test that fails on the pre-Phase-16 code (27
failures in `test_security_phase16.py` before the fixes, all passing after).

1. **IPv6 guard bypass** (`netguard.py:is_public_ip`). Python 3.11's
   `ip_address.is_global` reports `::/96` (IPv4-compatible, e.g.
   `[::127.0.0.1]`), `::ffff:0:0:0/96` (SIIT, e.g. `[::ffff:0:7f00:1]`),
   `64:ff9b:1::/48` (local-use NAT64) and `fec0::/10` (site-local) as global,
   so the guard would connect to them. On a host with a translator or
   site-local routing these reach private IPv4/IPv6 networks. They are now
   refused outright.
2. **Metadata reachable through an allowlist in embedded form**
   (`netguard.py:ip_policy`). With `fetch.allowed_networks` covering e.g.
   `::ffff:0:0/96`, `64:ff9b::/96` or `2002::/16`, the addresses
   `::ffff:169.254.169.254`, `64:ff9b::a9fe:a9fe` and `2002:a9fe:a9fe::` were
   allowed because only the literal metadata addresses were denied. Metadata
   is now refused in IPv4-mapped, 6to4, NAT64 and Teredo form whatever the
   allowlist says. (Default config was not affected: the allowlist is empty.)
3. **Credentials forwarded from the browser** (`fetch/browser.py:_forward`).
   A page could request `https://user:pw@host/...` or `https://token@host/`;
   the route forwarded it to `HttpFetcher.request_once`, which does not
   normalize, so the user info could be sent upstream (Basic auth) and logged.
   Such requests are now aborted, as a plain fetch already refuses them.
4. **Token in URL user name not redacted** (`log.py:JsonFormatter.redact`).
   Only `user:password@` was redacted; `https://ghp_xxx@github.com/` (a token
   as the user name) was logged in clear. Now redacted as `***@`.
5. **NEL splits log records** (`log.py:JsonFormatter.format`). JSON leaves
   U+0085 raw, and `str.splitlines` and some log viewers split on it, so page
   text in a field could forge a second log line. Now escaped like U+2028/9.
6. **Idempotency key replay across targets** (`api.py:create_crawl`).
   Reusing a key with a different `root_url` returned 200 and the *other*
   company's job (and its id), letting a guessed key reveal or hijack another
   request. Now 409 without details. An empty key (`""`) was accepted as a
   key; it is now 422.
7. **Hostile `Location` retried as a protocol error**
   (`fetch/http.py:classify`). httpx raises `RemoteProtocolError` while
   parsing an invalid `Location` header (e.g. `//[::1`), which was classified
   `PROTOCOL_ERROR` and retried. It is now `INVALID_REDIRECT`, not retried.
8. **API HTML surfaces** (`api.py:create_app`). Swagger UI and ReDoc (HTML
   pulling scripts from a CDN) were served at `/docs` and `/redoc`, and only
   `nosniff` was set. HTML docs are off; every response carries a
   `default-src 'none'` CSP, `X-Frame-Options: DENY` and
   `Referrer-Policy: no-referrer`.

Hardening, not an exploitable bypass before:

9. **Non-canonical numeric IPv4 hosts** (`urls.py:_normalize_host`).
   `http://2130706433/`, `http://0x7f.1/`, `http://127.1/`,
   `http://192.168.001.1/` were accepted as host *names*. glibc's resolver
   reads them as IPv4 (and the guard then refused the resolved loopback or
   private address), Windows' fails; so they were not reachable, but the URL
   layer and the resolver disagreed about the destination. They are now
   rejected at normalization (`test_urls.py` case moved accordingly).
10. **IDNA mapping to an IP literal** (`urls.py:_normalize_host`): a host
    whose IDNA form is an IP literal is normalized to the literal. Already
    the observed behaviour (tested now); the explicit branch keeps it so if
    the host handling upstream changes.

## Closed in Phase 18

Proven in the built image (`docker build -t gtm-crawler .`, Docker Desktop
29.5.3, WSL2 kernel 6.6, cgroup v2) by `deploy/sandbox-test/run.sh`; pytest
runs it when `GTM_SANDBOX_IMAGE` names the image
(`tests/test_hardening_phase18.py::test_sandboxed_render_in_container`,
skipped otherwise). The probe container runs as the production worker does:
the image's non-root user, `deploy/chrome-seccomp.json`, no added
capabilities, not privileged. Renders go through the crawler's own code path
(`HttpFetcher.fetch`, then `BrowserFetcher.render` with interception and the
guard) against a fixture on a private compose network.

1. **Chromium OS sandbox is on** (was gap 1). `browser.sandbox` (default
   true) is passed to Playwright as `chromium_sandbox`
   (`fetch/browser.py:BrowserFetcher._acquire`). Docker's default seccomp
   profile forbids the user namespaces the sandbox needs (verified: Chromium
   aborts with "No usable sandbox!"), so the worker runs with
   `deploy/chrome-seccomp.json`: Docker's default profile
   (github.com/moby/profiles `seccomp/default.json`, fetched 2026-10-01)
   plus one rule allowing `clone` and `unshare` with namespace flags.
   `CAP_SYS_ADMIN`, `--privileged` and `seccomp=unconfined` are not needed and
   not used. Trade-off: the worker container may create user namespaces,
   which exposes that kernel surface to the worker process; the renderer that
   parses hostile pages gets the full Chromium sandbox in return. Evidence
   (renderer processes sampled from `/proc` during the renders; the probe
   fails if any differs):

   | | worker (probe) | renderer, sandbox on | renderer, sandbox off |
   |---|---|---|---|
   | user namespace | 4026531837 | 4026532691 (own) | 4026531837 (shared) |
   | pid namespace (`NSpid`) | 4026532546 (`1`) | 4026532619 (`80 4 1`, nested twice) | 4026532546 (`210`) |
   | net namespace | 4026532548 | 4026532623 (own) | 4026532548 (shared) |
   | `Seccomp_filters` | 2 (Docker's) | 3 (Docker's + Chromium's seccomp-bpf) | 2 |
   | `NoNewPrivs` | 0 | 1 | 1 |

   `staging`/`prod` refuse `browser.sandbox = false` unless
   `browser.allow_unsandboxed = true` (`config.py:_check_prod`;
   `test_prod_refuses_browser_without_sandbox`). If the host forbids user
   namespaces (e.g. `user.max_user_namespaces = 0`, or Ubuntu 23.10+'s
   AppArmor userns restriction), Chromium does not start and the crawl goes
   on over HTTP with `stats.browser.unavailable` set: it never silently runs
   unsandboxed. Windows dev: the full suite (real-Chromium tests included)
   passes with the sandbox on.
2. **Worker memory is bounded by the container** (was gap 2).
   `docker-compose.yml` gives the worker `mem_limit: 2560m` (no swap).
   Measured cgroup `memory.peak` of the probe container (Python + Chromium,
   the worker's process structure; page cache included, as the limit counts
   it): two concurrent ordinary JavaScript renders ~240 MB; one worst-case
   page (10 MB of HTML, 200k DOM nodes) 1.1 GB; two of them at once 1.9 GB
   (anonymous memory 1.87 GB). The Python worker itself peaks at ~120 MB in
   the load tests (docs/load-results.md). 2.5 GiB leaves ~30% over the
   adversarial worst case and ~10x over ordinary renders. Over the limit the
   kernel's cgroup OOM killer picks by badness; Chromium gives its renderers
   `oom_score_adj` 300 (worker: 0), so a renderer goes first. Proven at a
   768 MiB limit with a page whose DOM grows without end: `memory.events`
   `oom_kill` 1, the render fails ("Page crashed", counted against the
   browser breaker), the worker process survives and the next render
   succeeds. If the worker process itself is killed (or the whole
   container), its job lease expires and another worker reclaims and resumes
   the job:
   `test_jobs.py:test_claims_are_exclusive_and_expired_leases_are_reclaimed`,
   `test_a_job_that_keeps_crashing_is_failed`,
   `test_queue_phase13.py:test_a_url_that_keeps_killing_its_worker_is_dead_lettered`,
   `test_recovery_after_a_crash_counts_lost_leases`, loadtest `crash_recovery`
   (a real worker process killed mid-crawl); the staging rehearsal replaces a
   worker container mid-crawl. Not proven in a container: the kernel choosing
   the worker process itself (it has the lowest score).
3. **Chromium DNS leakage is tested** (was gap 4). The fixture is the probe
   container's only DNS server and logs every query. The page tries every
   other way out on `*-leak.test` names (image, stylesheet, font, CSS
   background, DNS prefetch, preconnect, beacon, WebSocket, WebRTC STUN) and
   loads a script from `cdn-ok.test`. During the sandboxed renders the only
   names looked up were `acme.test` and `cdn-ok.test`, both resolved by the
   crawler's guard for requests it performed itself; no `*-leak.test` name.
   Control: plain Playwright Chromium (no interception, no proxy) on the same
   page looked up `beacon-`, `bg-`, `css-`, `font-`, `img-`, `stun-` and
   `ws-leak.test`, so the log does see browser lookups.

Also fixed (robustness, not security): when storing a page failed,
`crawl.py:_store_page` left the exception on a future nobody awaited
("Future exception was never retrieved"), and a later page with the same
text awaited that future and failed with the first page's error. The failure
is now observed, the claim on the fingerprint is withdrawn so later twins
store themselves, and the error still propagates (DB errors stop the crawl
resumably as before). `test_hardening_phase18.py` covers both.

## Gaps

Not fixed; each needs a deployment or architecture decision. (Gaps 1, 2
and 4 of Phase 16 are closed: see above.)

3. **Parse wall-clock limit: partly closed (2026-10-12).** A parse longer
   than `parse.timeout_seconds` (30 s) fails that URL with `parse_timeout`
   and the crawl goes on (`crawl.py:Crawler._parse`,
   `test_hardening_phase18.py:test_a_parse_that_takes_too_long_fails_the_url_not_the_crawl`).
   The parsing thread itself is not killed; it ends when the input, bounded
   by `fetch.max_body_bytes` and the element/link/depth caps, is consumed.
   Re-parsing stored pages at extraction (`EvidenceStore.load_page`) has no
   time limit.
5. **API abuse limits.** The API has a single optional bearer token
   (required in `prod`), `max_queued_jobs` backpressure and loopback binding
   by default, and, since 2026-10-12, `api.rate_limit_per_minute` (600) per connecting
   address on `/v1` (429 with `Retry-After`; in memory, per API process, so
   behind a proxy it counts the proxy), but no per-client job quota;
   `/metrics` and the health probes are unauthenticated by design (they
   expose counts, not content). A reverse proxy is expected to rate-limit.
6. **HTML consumers of the dossier.** The API never renders crawled text,
   but a downstream UI that inserts dossier strings as HTML would be
   vulnerable to stored XSS; the text is stored verbatim on purpose
   (evidence must match the source). Output encoding is the consumer's job
   and is documented here.

## Market research (2026-10-20)

The market engine (`gtm_crawler/market/`) adds user-supplied URLs (known
competitors), open-web search results, an optional model endpoint and a
new API surface. Controls, with their tests in `tests/test_market.py` and
`tests/test_market_service.py`:

* **SSRF / internal network.** Every market fetch (pages, robots.txt,
  OCR images) uses the crawler's `HttpFetcher` behind `NetGuard`: every
  redirect hop's resolved address is checked, metadata endpoints and
  non-public ranges refused, ports limited. A user-supplied URL to
  `169.254.169.254`, `127.0.0.1` or `10.0.0.0/8` ends `BLOCKED` with no
  connection made (`test_user_supplied_urls_cannot_reach_internal_addresses`;
  the guard refuses the robots.txt request first, which fails closed).
* **robots.txt and scope.** Checked for every page and image
  (`PolicyEngine.check(external=True)`); links are never followed (crawl
  depth 0); no login, paywall or form is ever submitted.
* **Resource limits.** Per-run `Budget` with validated maxima (queries,
  sources, pages per domain, per-request timeout and retries, body size,
  total duration); fetcher limits (size, decompression ratio, attempt
  timeout) tightened, never loosened; OCR images capped in bytes and pixels
  (`Image.MAX_IMAGE_PIXELS`); `market.max_queued_runs` backpressure (429);
  extraction bounded (600-character sentences, 40 records per source).
* **PDF files (untrusted).** Recognised by signature, downloaded through the
  same guarded fetcher (network guard on every redirect hop, robots.txt,
  throttle, timeouts, bounded retries) and capped at `market.pdf_max_bytes`.
  Parsed only in a child process (`python -m gtm_crawler.market.pdf`) that the
  parent kills at `market.pdf_timeout`; on POSIX it also runs under
  `RLIMIT_AS` (`pdf_memory_mb`) and a CPU-seconds limit; page and character
  caps stop large documents. pdfminer reads text and drawing operators only:
  no JavaScript, embedded files, links or form actions are executed or
  followed. Encrypted files are refused (never decrypted). The child's
  stderr (which may quote document content) is not logged; logs carry the
  URL, size and refusal code only. Limitation: on Windows there is no memory
  limit for the child, so a decompression bomb is bounded only by the
  timeout and the download cap (`tests/test_market_pdf.py`).
* **Browser renders (competitor pages only).** Since milestone 2 a known
  competitor's script-shell page or a pricing page stating no price is
  rendered with the crawler's `BrowserFetcher` (at most 8 per run): every
  request Chromium makes goes through the same `HttpFetcher` (network guard,
  robots.txt, throttle) and the browser containment of the crawler applies;
  a render that navigates to another domain is discarded
  (`test_js_rendered_competitor_pricing_is_rendered`).
* **Untrusted content and prompt injection.** By default no model sees page
  text: extraction is rules. With a model configured, excerpts are wrapped
  in `<evidence>` tags with `<`/`>` neutralized so a page cannot close its
  tag, the system prompt says tag content is data, and every answer
  sentence must cite evidence ids from the prompt, and state only numbers
  its cited excerpts state, or is dropped
  (`test_evidence_cannot_break_out_of_its_tag`,
  `test_ask_with_a_model_keeps_only_cited_sentences`,
  `test_model_sentences_must_use_the_cited_numbers`). Model output never
  feeds figures, sizing, scores or confidence, and is labelled
  model-generated.
* **Secrets.** `market.llm_api_key` is a `Secret` (redacted in logs, not in
  config snapshots or reports); the model URL must be https, or http with
  the host exactly `localhost`, `127.0.0.1` or `::1` (a prefix match let
  `http://localhost.evil.example` through until the 2026-10-21 audit;
  `test_llm_base_url_plain_http_only_to_this_machine`);
  error messages never include the model's response body.
* **Stored XSS.** Market routes are inside `create_app`, so the bearer
  token, rate limit and JSON-only security headers apply
  (`test_token_guards_market_routes`). The Markdown export escapes source
  and user text (`|`, `*`, `<`, `[`...).
* **Input validation.** Requests are pydantic models with `extra="forbid"`,
  length limits, enums and ranges; the server assigns `research_id` (a
  client cannot overwrite another run).
* **Cache poisoning.** Only this deployment's own finished reports are
  reused, by `subject_key`, and reused evidence is marked `cached_from`.
* **Concurrency.** Runs are leased with `FOR UPDATE SKIP LOCKED`; a lost
  lease stops the worker from writing; a run that keeps dying is failed
  after 3 attempts.

Gaps: the same API-abuse gap (5) applies to the new routes; OCR is not
live-tested (Tesseract absent in CI) and runs Tesseract on untrusted images
in-process (bounded by size, pixels and a 30 s timeout); dependency audit
(`pip-audit`) has not been run for the optional `[ocr]` extra.
