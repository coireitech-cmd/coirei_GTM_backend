# Implementation gap register

Gaps between the repository and the architecture-completion brief, found by
the Phase 0 audit (2026-10-12, branch `feat/identity-resolution`, base
`acba095`). One entry per gap; status is updated as iterations land. Progress
and test evidence per iteration: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

Severity: **Critical** (can store a wrong fact or merge two companies),
**High** (integrity or audit hole), **Medium**, **Low**.
Status: OPEN, IN PROGRESS, DONE, DEFERRED (not needed yet, reason given),
BLOCKED (needs a decision or external access).

## What already exists (not gaps)

The audit found these implemented and tested; they are reused, not rebuilt.

| Capability | Where |
|---|---|
| Evidence-first schema: `evidence` (quote + locator) -> `observation` -> `field_assessment`, append-only by trigger | `db/models.py`, migration 0001 |
| Quote must resolve on the cited page; stated value must be in the quote; qualifiers cannot be dropped | `db/evidence.py` `check_draft` |
| Field states UNKNOWN / SUPPORTED / CORROBORATED / CONFLICTING / STALE / UNSUPPORTED, kept apart from confidence | `assess.py` (fa-v2) |
| Full lineage: observation -> evidence -> page -> fetch -> artifact, with parser, extractor and method versions | `EvidenceStore.trace`, `field_lineage` |
| Content-addressed raw artifacts (SHA-256, dedup, retention, S3 or disk), replay and regression corpus | `rawstore.py`, `replay.py`, `corpus.py` |
| Central fetch policy ALLOW / BLOCK / DEFER / REJECT with reasons, audited in `crawl_event` | `policy.py`, `robots.py`, `crawl.py` `_refused` |
| SSRF guard on resolved IPs and every redirect, for HTTP and the browser | `netguard.py`, `fetch/`, docs/security-review.md |
| Search-provider interface, Exa optional, failover + circuit breaker, queries and results stored, cost recorded | `search.py`, `cost.py`, migration 0010 |
| Durable frontier, leases, idempotent jobs, DLQ, backpressure, metrics, tracing, backup/restore drill | phases 13-18, docs/production-readiness.md |
| Competitor domain resolution from a link on the evidence page; ambiguous stays unresolved | `pipeline.resolve_competitor_domains` |

## Phase 1: identity and entity resolution

### G-01 Organizations are merged on name alone — Critical — DONE (iteration 3)
- **Capability:** entity resolution of organizations named on a page (competitors, investors, partners).
- **Existing:** `db/evidence.py::_org_by_name` returns the lowest-id `organization` whose `lower(name)` equals the mentioned name, whatever its domain and whoever named it.
- **Missing:** any evidence that the mention and the matched row are the same company. A second company called "Mercury", named by an unrelated subject, attaches to the first one's row and inherits its domain and crawled facts.
- **Evidence:** `_org_by_name` (its own `ponytail:` note says exact-name only); `organization` has no scope column.
- **Dependencies:** none.
- **Security impact:** integrity; one tenant-like subject's evidence changes another's competitor.
- **Proposed change:** scope name-only rows to the organization whose pages named them (`organization.named_by_org_id`); never match a row by name across subjects.
- **Acceptance:** the same name mentioned by two subjects gives two rows; same subject, same name gives one row (idempotent); existing competitor resolution test still passes.

### G-02 A resolved competitor is not linked when its domain already has a row — High — DONE (iteration 3)
- **Existing:** `resolve_competitor_domains` writes the domain onto the name-only row only if no row has that domain; otherwise it reports the domain and leaves the two rows unrelated.
- **Missing:** a link from the mention to the organization that owns the domain. The dossier then shows the competitor as not assessed although its site was crawled.
- **Evidence:** `pipeline.py` `taken is None` branch; `dossier._build_dossier` reads fields by `relationship.object_org_id` only.
- **Proposed change:** `identity_link` (member row -> canonical organization), non-destructive; the dossier follows MATCH links.
- **Acceptance:** second subject naming an already-known competitor gets a MATCH link and the competitor's assessed fields; no row is updated or deleted.

### G-03 Resolution decisions are not recorded — High — DONE (iteration 3)
- **Existing:** a log line (`competitor.domain`) and a `basis` string in the job result.
- **Missing:** candidates, matching and contradicting signals, evidence ids, algorithm version, rationale, actor, automatic or manual, timestamp.
- **Proposed change:** append-only `identity_decision`; every resolution attempt writes one, including UNKNOWN and CONFLICTING.
- **Acceptance:** every link points to a decision; a decision names its evidence (`link.id`, `page.id`); reprocessing unchanged evidence writes no new decision.

### G-04 No model for external identities (profiles) — High — DONE (iteration 4)
- **Existing:** links from the site to LinkedIn, GitHub, Crunchbase, G2 and the like are stored as `link.is_reference`; a profile page fetched in the external phase is an ordinary `page`.
- **Missing:** the profile as a source identity (source type + external identifier), who claims it, and whether the claim is reciprocal.
- **Proposed change:** `external_identity` (unique per source type and identifier) linked to organizations through `identity_link`; MATCH only when the site links the profile and the fetched profile links back to the organization's domain; one direction is PROBABLE_MATCH; a profile claimed by two organizations is CONFLICTING.
- **Acceptance:** reciprocal links give MATCH; one-way gives PROBABLE_MATCH; two claimants give CONFLICTING and no MATCH; reruns add no rows.

### G-05 Search-found profiles are accepted by a name substring in the URL — High — DONE (iteration 5)
- **Existing:** `crawl.py::_external`: "A profile counts only if its URL names this exact company" is `slug in url.lower()`; the page is fetched and third-party extraction attributes statements to the subject wherever its name appears.
- **Missing:** identity evidence before attribution. `linkedin.com/company/acme-uk-plumbing` passes for subject "Acme".
- **Dependencies:** G-04.
- **Security impact:** wrong-entity facts in the dossier (state would be SUPPORTED at tier 2-3).
- **Proposed change:** extract from a reference-domain profile page only when its `external_identity` is MATCH or PROBABLE_MATCH for the subject (linked from the site, or linking back to it); otherwise keep the page and skip extraction, recorded as a decision.
- **Acceptance:** a same-name profile with no link either way yields no observations for the subject; a linked one still does; existing external-phase tests pass.
- **Done:** `resolve_profiles` records UNKNOWN for a fetched, unlinked profile; `extract.plan_extraction` skips pages on profile platforms unless `db.identity.attributable_pages` allows them (`identity_unverified_pages` in the extraction statistics). Limit: a one-way PROBABLE_MATCH is still read, as third-party evidence.

### G-21 Third-party pages are attributed by name alone — Critical — DONE (iteration 7)
- **Found by:** the live crawl of ghost.org on 2026-10-12. Search for `"Ghost" funding` returned articles and database pages about another company called Ghost (a B2B surplus-inventory marketplace). Their statements were stored as observations about ghost.org: `funding_round` $95M (cbinsights.com) reached the dossier as SUPPORTED; $30M, $103M, twelve investors, "B2B marketplace" and "retail" were stored and ended UNSUPPORTED or CONFLICTING.
- **Existing:** G-05 gated profile platforms only; any other page from search was read wherever the subject's name appeared.
- **Change:** `extract.plan_extraction` reads a page on another site only if it is the company's profile (MATCH or PROBABLE_MATCH), it links to the company's registrable domain, or the company's own site links to that exact URL (`db.identity.attributable_pages`). Other pages are kept and counted in `identity_unverified_pages`.
- **Acceptance:** an article about a same-name company with no link yields no observation; a linked article still corroborates; verified on the stored ghost.org crawl by re-extraction.
- **Limit:** one link is enough, so a page about two same-name companies that links to this one is still read. Fewer third-party facts overall: more fields are UNKNOWN.

### G-22 A person's profile can MATCH as the company's — High — DONE (iteration 8)
- **Found by:** the same run. `github.com/daviddarnes` and `github.com/cathysarisky` are individuals; ghost.org links to them and their pages link to ghost.org, so the reciprocal rule gave MATCH. Nine more personal accounts are PROBABLE_MATCH. Only `github.com/tryghost` is the company. GitHub URLs do not tell users from organizations.
- **Rule (approved 2026-10-12):** several reciprocal profiles of one platform for one company are all CONFLICTING (`several_reciprocal_profiles`); none is followed or read until a reviewer accepts one with `review_link`. A single reciprocal profile still MATCHes. On the stored ghost.org crawls the three GitHub accounts are now CONFLICTING.
- **Cost:** the company's real account (`github.com/tryghost`) waits for review too.

### G-23 Every profile a site links to is PROBABLE_MATCH — Medium — OPEN
- **Found by:** re-extracting the full ghost.org crawls: about 300 profiles each, almost all personal GitHub and X accounts of authors and contributors linked from the site, each PROBABLE_MATCH for the company. Such a page would be read as third-party evidence about the company if the external phase fetched it (it fetches at most `external.max_reference_pages`, 10).
- **Possible change:** a one-way site link counts only from the site's header, footer or structured data (`sameAs`), not from article bodies; or an adapter that asks the platform whether the account is an organization (G-10).

### G-24 Extraction errors found by the live runs — Medium — DONE (iteration 8)
- `/a-vs-b` pages where neither side is the company ("Data Analyst vs Data Engineer") gave competitors: one side must now be the company (competitor extractor v2).
- "Trusted by Thousands of Students Across Chennai" gave a customer: quantity words and bare numbers are not names (signals and traction extractors v4).
- "WordPress.Compare Ghost": two elements run together by the page gave one name; a name is cut at a full stop glued to the next capitalized word when more words follow.
- Not an error: the company name "TechPanda – Software Training & Placement Institute in Chennai" is the site's own JSON-LD `name`.

### G-06 No shared identity normalization — Medium — DONE (iteration 2)
- **Existing:** `extract.product.slugify`, `assess._fold`, `urls.registrable_domain`, each for its own purpose.
- **Missing:** company-name normalization (Unicode, punctuation, legal suffixes) and profile-URL -> (source type, external id), keeping the original value.
- **Proposed change:** `gtm_crawler/identity.py`; domains keep using `urls.registrable_domain`.
- **Acceptance:** table-driven tests; distinct companies are not collapsed ("Acme Holdings" != "Acme"); person and share URLs are not company identities.

### G-07 No review, reversal or supersession of an identity decision — High — DONE (iteration 3)
- **Missing:** a way to undo a wrong link that survives reprocessing.
- **Proposed change:** `reject_link` marks the link `rejected` with a manual decision that supersedes the automatic one; the resolver never re-creates a rejected pair. Dependent data needs no rewrite because facts stay on their own rows (links are followed at read time).
- **Acceptance:** after rejection the dossier no longer follows the link; rerunning resolution keeps it rejected; history shows both decisions.

### G-08 No identity evaluation corpus — Medium — DONE (iteration 5)
- **Done:** `tests/test_identity_eval.py`, 10 labelled cases; false merges 0, all outcomes as labelled.
- **Proposed change:** labelled cases (same name / different domain, reciprocal, one-way, conflicting, no evidence) scored for false merges, false matches, unknown accuracy, conflict detection. False merges must be 0.

### G-09 Mentions resolved by name only when the page has no link — Low — DEFERRED
- **Existing:** `unresolved: no_link` (UNKNOWN). Correct by the invariant.
- **Possible change:** list organizations whose own site states the same normalized name as review candidates (never auto-linked). Deferred until someone reviews such a queue.

## Phase 2-4: adapters, policy, profiles and artifacts

### G-10 No source adapter framework — Medium — OPEN
- **Existing:** every external page is generic HTML through the same fetch/parse/extract path; `SearchProvider` is the only provider-shaped interface.
- **Missing:** per-source declaration (source type, access mode, auth, rate policy, URL patterns, output schema version) and source-specific parsing (for example GitHub's public API).
- **Dependencies:** G-04 (adapters produce external identities and profile data).
- **Proposed change:** a small `SourceAdapter` protocol whose fetches go through `PolicyEngine` and `HttpFetcher`; first adapter GitHub organization (public REST, unauthenticated, rate-limited). No adapter for LinkedIn or other login-walled sources.
- **Acceptance:** adapter tests with mocked transport (ok, 404, 403 rate limit, malformed JSON, missing fields, policy refusal); adapter cannot fetch except through the policy engine.

### G-11 Policy engine has no per-source rules — Medium — OPEN
- **Existing:** scope, robots, crawl-delay, circuit state in `PolicyEngine.check`; traps and budgets in `Frontier`; network guard in the fetcher. Decisions are logged for refusals only.
- **Missing:** source-level rules (requires authentication -> BLOCK, API rate limit -> DEFER), and one place that lists them. `config.py` says reference domains are "never fetched" while the external phase does fetch them subject to robots.txt.
- **Proposed change:** source rules consulted inside `PolicyEngine.check`; fix the comment. Do with G-10.

### G-12 Source profile is not separated from page — Low — DEFERRED
- Artifact, fetch, page, evidence, observation and assessment are already separate records with hashes, versions and replay. A separate `source_profile` table is added only if an adapter (G-10) produces structured profile data that is not a page. Until then `external_identity` + `page` cover it.

## Phase 5-7: extraction, graph, discovery

### G-13 No LLM extraction layer — Medium — BLOCKED (decision + credentials)
- **Existing:** rule-based extractors only (approved 2026-09-29); alignment checks in `check_draft` cover exact quote, stated-value containment and dropped qualifiers.
- **Missing:** provider-neutral model interface, versioned prompts, bounded evidence packets, cache, cost tracking; alignment checks for negation, tense and intent ("exploring" stored as done).
- **Blocker:** choosing a provider and budget is a product decision; no model credentials in the repository. The deterministic alignment checks can be strengthened without it (G-14).

### G-14 Alignment checks do not cover negation, future or tentative statements — Medium — DONE (iteration 5)
- **Proposed change:** `check_draft` rejects or flags a stated observation whose quote negates or hedges the value ("not", "no longer", "plans to", "exploring", "expected to"); corpus cases added.
- **Acceptance:** the three examples of the brief (500+ customers, plans start at $49, exploring expansion) are covered by tests.
- **Done:** `check_draft` refuses a stated value that follows a negation or a plan word in its sentence (`_not_asserted`); "start at" / "as low as" prices are `gte` (product extractor v4) and cannot be stored without the qualifier. Limit: word list, text before the value only.

### G-15 Graph covers organizations and products only — Medium — DEFERRED
- **Existing:** `organization`, `product`, `relationship` (investor, tech_partner, strategic_partner, competitor; each cites an observation).
- **Missing:** person, funding event, market, technology, repository, news event as entities; validity dates on relationships.
- Added per entity type when an extractor or adapter produces it (G-10 brings `repository`). No graph database.

### G-16 FACT / INFERENCE / HYPOTHESIS are not all first-class — Low — DEFERRED
- Facts and derived values exist (`observation.kind` stated / derived; field states). Hypotheses belong to a GTM reasoning layer that does not exist yet (depends on G-13).

### G-17 Only one search provider — Low — DEFERRED
- Interface, failover and "search disabled" path exist and are tested. A second provider is added when one is licensed.

## Phase 8-10: security, operations, tenancy

### G-18 Known security gaps (docs/security-review.md 3, 5, 6) — Medium — DONE in part (iteration 6)
- **Done:** `parse.timeout_seconds` fails a slow parse's URL (`parse_timeout`); `api.rate_limit_per_minute` per connecting address on `/v1`. **Left:** the parsing thread is abandoned, not killed; no limit on re-parsing at extraction; the rate limit is per process; output encoding stays the consumer's job.
- No parse wall-clock limit; no per-client API rate limit; dossier text must be output-encoded by HTML consumers.

### G-19 Live verification — High — VERIFIED IN PART (2026-10-12)
- All acceptance evidence is against a simulated web (docs/production-readiness.md). Real Chromium cost and multi-host workers are unverified.
- **Live run 2026-10-12** (after migration 0015, local database): `https://itechpanda.com`, bounded to 50 pages and 2 competitor crawls, live Exa. Exit 0 in 65 s; 84 requests, 41 pages extracted, 0 rejected, 3 search queries; 3 profiles PROBABLE_MATCH; no observation taken from the same-name companies the search returned. The database also holds live crawls of five domains from 2026-10-03. The README status line was corrected.
- **Found by the run (open):** "X vs Y" blog titles read as competitors when neither side is the company; "Trusted by thousands of students" read as a customer; a page title with a mis-decoded dash stored as a company name; most third-party sources answer 403 or are disallowed by robots.txt, so external corroboration is thin; cost is reported as 0 because no unit prices are configured.
- **Still not verified:** more than one site, a full-size crawl, a competitor crawl on a live site, the browser fallback on a live site, any deployment.

### G-20 No tenant model — Medium — BLOCKED (product decision)
- **Existing:** one optional bearer token for the whole API; organizations, artifacts and evidence are global.
- **Boundary today:** single-tenant; one deployment per customer. Public-web evidence is shareable by design, but jobs, dossiers and cost are not partitioned.
- **Needed before multi-tenant use:** tenant on `crawl_job` and API principals, authorization at the store layer, cross-tenant denial tests. G-01's per-subject scoping removes the one place where subjects affected each other's data.
