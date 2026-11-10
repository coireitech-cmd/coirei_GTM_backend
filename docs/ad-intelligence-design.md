# Competitor Ad Intelligence — Architecture and Discovery

Status: Phase 1 (MVP) implemented on `feat/ad-intelligence` (2026-11-06);
P0 decisions (§13, legal sign-off D1 first) still open. Assessment date: 2026-11-06.
Scope: `gtm-crawler/gtm_crawler/` (crawler + market engine `gtm_crawler/market/`).

## Implementation status (Phase 1)

| Piece | Where |
|---|---|
| `ad`, `ad_observation` (append-only trigger, unique `(platform, platform_ad_id)` and `(ad_id, import_id)`) | migration `0019_ad_observation`, `db/models.py` |
| Capability table, import validation, storage, `load_ads`, `ad_evidence`, `ad_section`, `performance_claims` | `market/ads.py` |
| `SourceType.AD_LIBRARY`, `Dimension.MESSAGING`, locator `ad`, `ProductInput.include_ads`; `SCHEMA_VERSION` 3, `ANALYSIS_VERSION` market-analysis-3 | `market/models.py`, `market/analyze.py` |
| Ads loaded like the dossier; landing pages join the competitor pages (`max_pages_per_domain` each); `competitor_ads` section | `market/engine.py` |
| `ad` quotes re-checked against the stored capture | `market/verify.py` |
| `ad_claims` gate | `market/benchmark.py`, docs/MARKET_BENCHMARK.md |
| Ad-library hosts BLOCKed for every fetch (`ad_library`) | `policy.py` |
| `gtm-market --import-ads FILE --operator NAME [--screenshots DIR]`; `include_ads` opens the database | `market/runner.py` |
| Prune keeps ad captures and screenshots | `cli.py` |
| Capture checklist from a crawled company: competitors (and co-listed candidates) with Meta Ad Library / Ads Transparency Center search links to open by hand, and what is captured — `gtm-market --ad-checklist COMPANY_ID [--country IN] [--markdown]` | `market/ads.py` (`capture_checklist`), `market/runner.py` |
| `include_ads` with a `company_id`: the dossier's resolved competitors' ads join the report (known competitors without ads: warning; dossier ones: info); the dossier is read once, before collection | `market/engine.py` |
| Tests | `tests/test_market_ads.py` |

Differences from the design below, deliberate:
- Ad evidence has dimension `messaging` only; themes (pain points, pricing, …)
  are computed inside the ad section. Otherwise an advertiser's copy would
  answer market questions ("demand", "pain points") as if it were market
  evidence.
- Check constraints allow only `method = manual_import` and verification
  `operator_transcribed | ocr_corroborated`; an API adapter widens them in its
  own migration.
- Landing pages are fetched only when the destination's registrable domain is
  the advertiser's (covers click trackers and third-party sites in one rule).
- Co-listed candidate competitors (PR #11) appear in the checklist with a Meta
  name search only: they have no domain, and ads are matched to a company by
  the domain the operator imports them under.
- Checklist search URLs are built from the sites' public URL parameters
  (Meta `q`, `country`; Google `region`, `domain`); they are never fetched,
  and an operator should confirm they still open the expected search.
- Change-over-time findings (P2) are not built: every observation is stored,
  only the latest per ad becomes evidence.
- **Not done: the 30–50-ad evaluation set (§10).** It needs an operator to
  capture real ads, after D1. Tests use synthetic records.

Import file (one object per ad; field values `{"state", "verbatim", "normalized"}`,
`verbatim` exactly when `state` is `present`; card fields take a list, one text
per card; `normalized` only for `platform_start` / `platform_stop`, ISO date):

```json
{"ads": [{
  "platform": "meta", "platform_ad_id": "1234567890123",
  "observed_at": "2026-11-01T10:00:00+05:30",
  "advertiser": {"advertiser_id": "987654321", "name": "Acme",
                 "profile_url": "https://www.facebook.com/ads/library/?view_all_page_id=987654321",
                 "competitor_domain": "acme.example"},
  "fields": {
    "headline": {"state": "present", "verbatim": ["Switch from MyCRM in 7 days"]},
    "body": {"state": "present", "verbatim": ["Stop losing leads. Start a free trial."]},
    "cta": {"state": "present", "verbatim": "Sign up"},
    "format": {"state": "present", "verbatim": "Image"},
    "destination_url": {"state": "present", "verbatim": "https://acme.example/crm?utm_source=fb"},
    "platform_start": {"state": "present", "verbatim": "Started running on 1 Oct 2026",
                       "normalized": "2026-10-01"},
    "regions": {"state": "not_collected"}
  },
  "screenshots": ["acme-1234567890123.png"]
}]}
```

---

## 0. Feasibility verdict and source matrix

**Verdict.** No permitted automated path exists for **commercial ads delivered
in India** on either platform. Both platforms offer automated access only for
EU/EEA-delivered ads (and, for Meta, political/issue ads everywhere). The
capability is therefore **manual-import first**, with optional platform-API
adapters for EU/EEA coverage later. This is a small extension of the existing
market engine, not a new engine.

| Source | Geography / ad type | Method | Permitted? | Fields available | Notes |
|---|---|---|---|---|---|
| Meta Ad Library | India, commercial | Official API (`ads_archive`) | Not available | — | "Ads that did not reach any location in the EU will only return if they are about social issues, elections or politics." [M1] |
| Meta Ad Library | India, commercial | Automated scraping of web UI | **No** (without Meta's express written permission) | — | Automated Data Collection Terms §2, §3, §8 [M3]; `facebook.com/robots.txt` ends `User-agent: *` / `Disallow: /` [M4] |
| Meta Ad Library | India, commercial | Human views UI, operator imports | Likely yes — **legal to confirm** | Whatever operator records (text, CTA, Library ID, start date, screenshot) | Not "automated collection" as defined in [M3] §8, provided no bot/browser automation is used |
| Meta Ad Library | EU/UK, all ads | Official API | Yes (token; Meta's identity/location confirmation — page returned 403 to fetcher, confirm manually [M2]) | id, creative bodies, link titles/descriptions/captions, `ad_delivery_start_time`, `ad_delivery_stop_time`, snapshot URL, page id/name, publisher platforms, languages; EU only: `eu_total_reach`, `beneficiary_payers`, `target_locations` [M5] | Rate limit exists (error 613); numeric limits not documented on the reference page [M1] |
| Meta Ad Library | Any, political/issue | Official API | Yes | Above + `impressions`, `spend` as ranges [M5] | Out of scope for SaaS competitor research (see Non-goals) |
| Google Ads Transparency Center (ATC) | India, commercial | Official API | Not available | — | Google: "Google provides API access to data about ads served in the EEA"; no public API for other regions described [G1] |
| Google ATC | India, commercial | Automated scraping of web UI | **Not permitted by design** | — | `adstransparency.google.com/robots.txt` returns 404 (no machine-readable rule), but ATC data use is "subject to the Ads Transparency Center terms of service" [G1]; that terms page is JS-rendered and could not be read by tooling → **legal must read it**. Google ToS forbids automated access violating machine-readable instructions [G3]. Treat as not permitted until legal says otherwise. |
| Google ATC | India, commercial | Human views UI, operator imports | Likely yes — **legal to confirm** | Operator-recorded text, format, advertiser ID (`AR…`), creative ID (`CR…`), first/last shown dates, screenshot | |
| Google ATC | EEA + Turkey | Public dataset (BigQuery `bigquery-public-data.google_ads_transparency_center` or free JSON download) | Yes, under Google ToS + ATC Additional Terms [G2] | `creative_stats`: advertiser id/legal name/disclosed name/location/verification status, `creative_id`, `creative_page_url`, `ad_format_type`, `topic`, per-region `first_shown`/`last_shown`, times-shown ranges, audience-selection approach [G2] | **No creative text, headline or CTA in the dataset.** Metadata only. Update cadence not stated. |
| Third-party vendors (SerpApi, Apify actors) | Any | Vendor scrapes UI | Vendor's legal posture; COIREI inherits terms risk | Varies | Procurement + legal decision, not a default (§13) |

Sources (all retrieved 2026-11-06):

- [M1] Meta Graph API `ads_archive` reference — https://developers.facebook.com/docs/graph-api/reference/ads_archive/
- [M2] Meta Ad Library API page — https://www.facebook.com/ads/library/api/ (HTTP 403 to automated fetch; read manually)
- [M3] Meta Automated Data Collection Terms — https://www.facebook.com/apps/site_scraping_tos_terms.php
- [M4] https://www.facebook.com/robots.txt
- [M5] Meta Graph API `ArchivedAd` reference — https://developers.facebook.com/docs/graph-api/reference/archived-ad/
- [G1] Google Ads policy "Ads transparency" — https://support.google.com/adspolicy/answer/13733850
- [G2] Google ATC dataset README — https://storage.googleapis.com/ads-transparency-center/api-data/README.txt ; BigQuery listing https://console.cloud.google.com/marketplace/product/bigquery-public-data/google-ads-transparency-center (listing not machine-readable; README used)
- [G3] Google Terms of Service — https://policies.google.com/terms
- `https://adstransparency.google.com/robots.txt` → HTTP 404; `https://adstransparency.google.com/terms` → JS-rendered, content not retrievable by tooling.

No automated request was sent to either Ad Library UI or any internal endpoint.

---

## 1. Current repository state

| Item | Value |
|---|---|
| Branch | `feat/competitor-discovery` @ `a835618` ("broad competitor discovery and candidate tier") |
| Status | clean (only this file added, untracked) |
| Relation to main | 1 commit ahead of `origin/main` (`d7b3b32`, PR #8 merge); open as PR #11, unmerged |
| Other worktrees | `coireigtm` (feat/market-engine), `market-audit` (fix/locale-number-formats), `postman-market`, `root-readme` — not touched |
| Latest migration | `0018_candidate_competitor` (from PR #11; `0017` on main) |

**PR #11 dependency.** The MVP does **not** depend on PR #11. Advertisers to
look up come from `ProductInput.known_competitors` or the crawled dossier's
competitors (`company_id`). PR #11's `candidate_competitor` relationships are a
useful *suggestion list* for operators, not an input the design needs. The only
coupling is migration numbering: the new migration is `0019` with
`down_revision = "0018"` if PR #11 merges first, otherwise `0018` on main and
PR #11 renumbers.

Unmerged/uncommitted hardening elsewhere (evidence-support, pricing authority
in other worktrees) is **not assumed** by this design; it touches extraction
quality, not the seams used here.

---

## 2. Existing modules and reusable infrastructure

### Market engine (`gtm_crawler/market/`)

| Module | What it gives this feature |
|---|---|
| `models.py` | `SourceRecord`, `SourceType`, `RetrievalStatus`, `EvidenceRecord`, `EvidenceKind` (DIRECT / DERIVED / INFERRED / USER), `Locator` (types `xpath|json_path|meta|dossier|image|pdf`), `Dimension`, `Finding`, `Confidence`, `Section`, `Report`, `Issue`, `Competitor`, `ProductInput`. |
| `engine.py` | `run_research` orchestration; **`dossier_evidence()`** (lines ~180–231) converts DB-held, cited facts into `SourceRecord` + `EvidenceRecord` for a run — the exact pattern ads need; `_ocr_evidence` shows how image text becomes evidence with an `image` locator and reduced certainty. |
| `collect.py` | `Collector`: guarded `HttpFetcher` + `RobotsCache` + `PolicyEngine` + `HostThrottle` + rawstore + optional guarded `BrowserFetcher`. Directly reusable for **landing-page inspection**. |
| `extract.py` | Rule-based evidence extraction: `extract_evidence`, `dimensions_of(sentence)`, `sentences`, `relevance`, `subject_terms`, money parsing. Reusable for value-prop / pain-point / offer detection on ad text and landing pages. |
| `pricing.py` | Price parsing with context — reusable for offers that state prices/discounts. |
| `analyze.py` | Section builders (`PRODUCT_SECTIONS`, `product_sections`, `_section`, `_finding`, `_inferred`, `differentiation`, `competitor_coverage`). New ad section follows these. |
| `assessment.py` | `classify`, `find_contradictions`, `domain_of`. |
| `verify.py` | `verify_evidence` / `verify_report`: re-derives quotes from stored raw artifacts. Extend with an `ad` locator branch. |
| `ocr.py` | `Ocr.read_bytes(data)` — OCR of raw image bytes with decompression-bomb guard. Reusable for screenshots. |
| `llm.py` | Optional OpenAI-compatible narrative; deterministic TF-IDF retrieval; evidence passed as quoted data (prompt-injection guard); sentences without valid citations dropped; labelled model-generated. Reusable as-is for an optional narrative over ad evidence. |
| `report.py` | `to_markdown(Report)`. New section renders without changes if it is a normal `Section`. |
| `store.py` | `MarketStore` (lease queue; report JSON; `cited_digests` for retention). |
| `benchmark.py` | Deterministic gates (provenance, citations, numeric, scope, unsupported, status). Add an ad case set + one new gate. |
| `api.py`, `runner.py` | FastAPI routes under `/v1/market/research…`; CLI `main(argv)`. |

### Crawler infrastructure (`gtm_crawler/`)

| Module | Reuse |
|---|---|
| `db/models.py` `ExternalIdentity`, `IdentityDecision`, `IdentityLink` | Advertiser platform profiles ("a profile on another platform, not a claim about whose it is") + auditable, reversible link to an `organization`. Fits Meta Page / Google advertiser IDs exactly. |
| `db/models.py` `Observation`, `FieldAssessment`, `Evidence` | Patterns to copy (append-only, `value_verbatim` + `value_normalized`, DB check constraints refusing unsupported values). **Not reusable as tables**: `Evidence.page_id` and `Observation.crawl_id/extraction_id/evidence_id` are NOT NULL and bound to crawled pages. |
| `db/models.py` `CostEvent` | Cost accounting (`COST_KINDS` = http, browser, search, storage, database, worker). |
| `rawstore.py` | Content-addressed `put/get/exists/prune` (local or S3); 90-day retention (`rawstore.retention_days`), "kept while cited". Stores API responses, import files, screenshots. |
| `netguard.py`, `robots.py`, `policy.py`, `throttle.py`, `breaker.py`, `fetch/` | SSRF guard, robots, policy, per-host throttle, circuit breaker for any HTTP the adapters or landing-page fetches make. |
| `urls.py` | `normalize_url`, `registrable_domain` — destination URL normalization. |
| `identity.py` | `normalize_name`, external id parsing — advertiser name comparison (never proof of identity). |
| `db/migrations/` (Alembic) | Schema changes. |
| `search.py` (Exa) | Not needed for ads. |

**Conclusion:** persistence for the *history* of ads is the only genuinely
missing infrastructure. Everything else — fetching, guarding, raw storage,
OCR, extraction, evidence, verification, reporting, benchmarking, identity —
exists.

---

## 3. Proposed component boundaries

| Boundary | Proposal | Reuse / extend / new |
|---|---|---|
| Ad data models + persistence | Two tables `ad`, `ad_observation` (§4); advertiser = `external_identity` | **New** tables; **reuse** `external_identity`/`identity_link` |
| Source capability table | One constant dict in a new `market/ads.py`: per (platform, method) → permitted, geographies, fields, needs_credentials, citation. Read by adapters and the report's limitations. No registry class. | **New** (data, ~40 lines) |
| Collection adapters | One `AdSource` Protocol (§5). MVP implementation: `ManualImport` only. Later: `MetaAdLibraryApi` (EU/UK), `GoogleAtcDataset` (EEA/Turkey metadata). | **New**; HTTP via **reused** `HttpFetcher`/throttle/breaker |
| Normalization + dedup | Ad identity = `(platform, platform_ad_id)` unique constraint. Content hash per observation for change detection; optional cross-platform cluster by content hash, never merging identity. URL normalization via `urls.normalize_url` + tracker-parameter stripping. | **New** (small); **reuse** `urls.py` |
| Creative metadata extraction | Text fields as captured; screenshot OCR via `Ocr.read_bytes` to corroborate operator transcription. Video: store reference only. | **Reuse** `ocr.py`; **new** glue |
| Landing-page inspection | Fetch normalized destination through `Collector` (robots, netguard, throttle, rawstore), parse with existing parser, extract evidence with `extract_evidence`. Never fetch ad click-tracking URLs. | **Reuse** `collect.py`, `extract.py`; **extend** `policy.py` deny-list |
| Messaging + positioning analysis | Deterministic: `dimensions_of` on ad sentences, CTA/format/offer tallies, `pricing` on offers, landing alignment by term overlap and offer/price presence. Optional `llm.py` narrative. | **Reuse** `extract.py`, `pricing.py`, `llm.py`; **new** `ad_section()` in `analyze.py` |
| Evidence validation | `ad` locator type; `verify_evidence` re-derives excerpt from stored raw capture; benchmark gate for forbidden inferences. | **Extend** `models.Locator`, `verify.py`, `benchmark.py` |
| Report integration | `ad_evidence()` mirroring `dossier_evidence()`; new product-mode section `competitor_ads`. | **Extend** `engine.py`, `analyze.py`; **reuse** `report.py` |
| Import entry point | CLI subcommand on `market/runner.py` in MVP; API route later. | **Extend** `runner.py` |

---

## 4. Data models and relationships

```
organization ──< identity_link >── external_identity  (advertiser profile, existing)
                                        │ 1
                                        │
                                        ▼ n
                                       ad   (platform, platform_ad_id)  UNIQUE
                                        │ 1
                                        ▼ n
                                  ad_observation  (append-only, one per capture)
                                        │
                                        └── raw_sha256 / media[].sha256 ──► rawstore
```

### Advertiser — reuse `external_identity`

- `source_type`: `meta_ad_library_page` | `google_atc_advertiser`
- `external_id`: Meta Page ID / Google `AR…` advertiser ID
- `profile_url`: Ad Library / ATC advertiser URL as first seen
- Link to a company through `identity_decision` + `identity_link` (existing,
  reversible, auditable). An advertiser→organization link is an **identity
  claim with a decision and rationale**, never a string match.
- Advertiser **domain** is not stored as a fact. It is derived from the linked
  organization. Google's `advertiser_verification_status` / legal name, when
  present, are observation fields, not identity proof.
- Advertiser display names change; they are stored per observation.

`identity_decision.signals` currently names `link_id`/`page_id`; an operator's
manual decision (`automatic=false`, `actor=<operator>`) fits without schema
change. `external_identity.source_type` has no check constraint (model and
migration `0015` define only `UNIQUE(source_type, external_id)`), so the two
new source types need no migration.

### `ad` (new)

| Column | Type | Notes |
|---|---|---|
| id | bigint PK | |
| platform | text, check in (`meta`, `google`) | |
| platform_ad_id | text NOT NULL | Meta Library ID; Google `CR…` creative ID |
| external_identity_id | FK `external_identity` NOT NULL | advertiser profile |
| created_at | timestamptz | first time COIREI recorded it |

`UNIQUE(platform, platform_ad_id)`. Nothing else: everything that can change
lives on observations.

**Ads without a platform ID** (operator has only a screenshot): refused in MVP.
Both UIs show the ID (Meta "Library ID", Google `CR…` in the URL), so
requiring it costs the operator seconds and keeps identity exact. (Decision
D6.)

### `ad_observation` (new, append-only)

| Column | Type | Notes |
|---|---|---|
| id | bigint PK | |
| ad_id | FK `ad` | |
| observed_at | timestamptz NOT NULL | when COIREI (or the operator) saw it |
| method | text check in (`manual_import`, `meta_api`, `google_atc_dataset`) | vendor methods added only by decision D2 |
| adapter / adapter_version | text | |
| import_id | text | groups one import batch / API run; idempotency key |
| operator | text NULL | required when `method = manual_import` (check constraint) |
| raw_sha256 | text NOT NULL | rawstore digest of the exact capture: API JSON page, or the import record as submitted |
| content_sha256 | text NOT NULL | hash of normalized creative content (body, headline, description, CTA, destination, media hashes) — change detection and clustering |
| fields | jsonb NOT NULL | per-field `{state, verbatim, normalized}` (below) |
| media | jsonb | list of `{kind: image|video, source_url, sha256|null, ocr_text|null}` |
| verification | text check in (`platform_record`, `operator_transcribed`, `ocr_corroborated`) | |
| created_at | timestamptz | |

`UNIQUE(ad_id, import_id)` makes a retried import idempotent.

**`fields` keys** (each `{state, verbatim, normalized}`):

| Key | Example source |
|---|---|
| advertiser_display_name | Meta `page_name`; ATC disclosed name |
| advertiser_legal_name, advertiser_location, advertiser_verification | ATC dataset only |
| body | Meta `ad_creative_bodies[]` (list kept, one per card) |
| headline | Meta `ad_creative_link_titles[]` |
| description | Meta `ad_creative_link_descriptions[]` |
| caption | Meta `ad_creative_link_captions[]` |
| cta | button text as shown |
| format | `image` / `video` / `carousel` / `text` (ATC `ad_format_type` mapped; raw kept in verbatim) |
| destination_url | as displayed; `normalized` = tracker-stripped, `urls.normalize_url` |
| platforms | Meta `publisher_platforms`; ATC surface if shown |
| languages | Meta `languages` |
| platform_start | Meta `ad_delivery_start_time` (*advertiser's intended start*), Meta UI "Started running on", ATC `first_shown` |
| platform_stop | Meta `ad_delivery_stop_time` (*intended stop; blank = running until stopped*), ATC `last_shown` |
| regions | only regions the platform states (ATC `region_stats.region_code`, Meta `target_locations` for EU/UK) |
| disclosed_reach | Meta `eu_total_reach`; ATC times-shown ranges — **verbatim only** |
| platform_topic | ATC `topic` |

**Field states** (check-constrained in application validation, stored in JSON):

| State | Meaning |
|---|---|
| `present` | value captured |
| `absent_on_source` | source shows the field and it is empty (e.g. ad has no headline) |
| `not_collected` | method cannot or did not capture it (e.g. creative text via ATC dataset) |
| `inaccessible` | source has it but COIREI cannot access it (login, region, blocked) |
| `redacted` | source shows it removed/withheld |

`verbatim` is mandatory when `present`; `normalized` only when the verbatim
supports an exact typed value (the `Observation` rule).

**Dates — two separate clocks.**
- Platform clock: `platform_start` / `platform_stop` as stated, with their
  platform semantics recorded (Meta's are *intended* delivery times; Google's
  are *shown* dates per region).
- COIREI clock: `observed_at` per observation. The "observed window" of an ad
  is `min/max(observed_at)`.
- Neither is ever derived from the other. "Not in the latest capture" is
  *not observed*, never *stopped*, unless `platform_stop` says so.

**Deduplication.** Never merge across platforms or across platform IDs. Two
observations of one ad with equal `content_sha256` are "unchanged"; different
hashes are a creative change. A cross-platform "same creative" cluster
(equal normalized body+headline) is computed at analysis time, not stored, in
MVP.

**Why new tables instead of existing ones** (rule 8):
- `observation`/`evidence` require `crawl_id`, `extraction_id`, `page_id` — ads
  are not crawled pages; loosening constraints on a shared append-only table
  weakens guarantees the crawler relies on.
- `market_research.report` JSON is per run and immutable; it cannot answer
  "how did this competitor's ads change over three months".

---

## 5. Collection adapter contract

```python
class AdQuery(Strict):
    platform: Literal["meta", "google"]
    advertiser_ids: list[str] = []          # Page IDs / AR ids
    search_terms: str | None = None
    countries: list[str] = []               # ISO codes; adapters refuse unsupported ones
    delivered_since: date | None = None

class AdCapture(Strict):
    platform: Literal["meta", "google"]
    platform_ad_id: str
    advertiser_id: str
    advertiser_profile_url: str
    observed_at: datetime
    fields: dict[str, FieldValue]           # FieldValue = {state, verbatim, normalized}
    media: list[MediaRef]
    raw: bytes                              # exact capture; stored in rawstore by the caller
    verification: Literal["platform_record", "operator_transcribed", "ocr_corroborated"]

class AdSource(Protocol):
    name: str
    version: str
    def capability(self) -> Capability: ...          # row of the static capability table
    async def collect(self, query: AdQuery) -> AsyncIterator[AdCapture]: ...
```

Rules every adapter obeys:
1. **Refuse, don't degrade:** a query outside the capability row (e.g. Meta API
   with `countries=["IN"]` and non-political ads) raises before any request,
   naming the limitation. No silent empty result.
2. All HTTP through the existing `HttpFetcher` + `HostThrottle` + `breaker`;
   no new HTTP client.
3. `raw` is the unmodified bytes received/submitted; the caller stores it via
   `rawstore.put` and persists `raw_sha256`.
4. Fields the method cannot provide are `not_collected`, never omitted.
5. Credentials (Meta token) from config/secrets, never logged.

**`ManualImport` (MVP).** Input: a JSON file (one object per ad; schema =
`AdCapture` minus `raw`/`observed_at`, plus `observed_at` as operator-stated
capture time) and optional screenshot files referenced by name. Validation at
the trust boundary: schema (`Strict` pydantic), size caps, image type sniffing,
`MAX_PIXELS`. Screenshots → rawstore; OCR via `Ocr.read_bytes`; if OCR text
fuzzy-contains the transcribed body/headline, `verification = ocr_corroborated`,
else `operator_transcribed`. CSV is not supported in MVP (JSON is unambiguous
for lists such as carousel cards).

**`MetaAdLibraryApi` (Phase 3, only if D4 = yes).** `GET ads_archive` with
`ad_reached_countries` limited to EU/UK codes, `search_page_ids` (≤10 per call
[M1]), paging until empty `data`. Error 613 → breaker/backoff.

**`GoogleAtcDataset` (Phase 4, only if D4 = yes).** Metadata only (no creative
text). Prefer the public JSON download over BigQuery to avoid a new
dependency; size and cadence must be measured first (unknown).

---

## 6. Source limitations and legal/operational considerations

- **India commercial ads: manual only** on both platforms (§0).
- **Meta:** automated collection from Meta products needs express written
  permission; accepting terms is not permission [M3]. This includes
  browser-automation tools (Playwright, extensions) driving the Ad Library UI.
  Media URLs (`ad_snapshot_url`, CDN images) require a token or may expire —
  capture bytes at collection time or record `inaccessible`.
- **Meta API semantics:** `ad_delivery_start_time`/`stop_time` are the
  advertiser's *intended* times, not observed delivery [M5]. Report wording must
  say "scheduled to start", not "started".
- **Google:** no public API outside the EEA [G1]. Public dataset = EEA/Turkey
  metadata without creative text [G2]. ATC terms page must be read by legal
  before any use beyond manual viewing.
- **Crawler leakage risk (found in this assessment):** `adstransparency.google.com`
  has no robots.txt (404), so the crawler's robots check would *allow* fetching
  it if search or a link ever surfaced such a URL. Add an explicit host deny-list
  to `policy.py` for `adstransparency.google.com` and `facebook.com/ads/library`
  (the latter is already blocked by Meta's robots `Disallow: /`, but an explicit
  rule documents intent and survives robots changes).
- **Never fetch ad click URLs.** Destination links in ads may be click trackers
  (e.g. `googleadservices.com/pagead/aclk…`, `l.facebook.com/l.php?u=…`).
  Fetching them can register a paid click on the competitor's account. Landing
  inspection fetches only the displayed destination after stripping tracking
  parameters (`gclid`, `fbclid`, `utm_*`, `msclkid`, …); if only a tracker URL
  exists, the landing field is `not_collected`.
- **Copyright / storage:** ad creatives are the advertiser's copyrighted
  material. Store extracted text, hashes, and screenshots for internal analysis
  only; do not redistribute media in client deliverables without legal
  approval (D3).
- **Personal data:** Meta advertisers can be individuals' Pages; creatives can
  show people. Store Page name/ID only; no face or person analysis.
- **Coverage bias:** manual capture covers what operators chose to look at.
  Every report section states capture method, date range and count, and that
  absence of ads is not evidence a competitor does not advertise.

---

## 7. Persistence and historical observation strategy

- Migration `0019_ad_observation` (or `0018` if PR #11 lands later): create
  `ad`, `ad_observation`, constraints, indexes `(ad_id, observed_at)`,
  `(external_identity_id)` on `ad`.
- **Append-only** by convention like `observation`/`identity_decision`: no
  update path in code; a test asserts the store module issues no `UPDATE`/
  `DELETE` on `ad_observation` outside retention pruning.
- "Current state" of an ad = its latest observation; history = all of them.
- **Retention:** raw captures and screenshots go into the rawstore. Extend the
  prune keep-set (as `MarketStore.cited_digests` did on 2026-10-23) with
  `ad_observation.raw_sha256` and `media[].sha256` so verification never breaks
  while the observation row exists. Whether ad observation rows themselves expire
  is decision D3 (default: kept; rows are small).
- **Market report linkage:** reports keep embedding `SourceRecord`/
  `EvidenceRecord` JSON as today. Ad evidence carries `ad_observation.id` in
  its locator, so a report remains verifiable against the rawstore without the
  ad tables (same independence as dossier evidence).

---

## 8. Analysis and evidence-validation workflow

```
import / adapter ─► ad_observation (+ rawstore) ─► ad_evidence(req) ─► EvidenceRecords
                                                         │
     landing pages via Collector ─► extract_evidence ────┤
                                                         ▼
                              analyze.ad_section() ─► Section(findings, gaps)
                                                         ▼
                                   verify_report / benchmark gates
```

**Step 1 — `ad_evidence(req, store)`** (new, mirrors `dossier_evidence`). For
the request's competitors (`known_competitors` or dossier competitors) with a
`match` identity link to an advertiser profile, load the latest observation of
each ad (plus earlier ones for the change view). Each present text field
becomes one `EvidenceRecord`:
- `kind = DIRECT`, `excerpt` = verbatim text, `claim = "<advertiser> ad
  (<platform> <id>) states: <text>"` — an ad evidences **what the competitor
  says**, never that it is true.
- `locator = Locator(type="ad", value="<platform>:<platform_ad_id>#obs:<id>:<field>[<i>]")`
  (add `ad` to the `Locator.type` pattern).
- `SourceRecord(source_type=SourceType.AD_LIBRARY, url=<ad library URL>,
  raw_sha256=<capture digest>, retrieved_at=observed_at, status=CACHED,
  discovered_by="ad_import"|"meta_api"|…, publisher=<advertiser>)`. New
  `SourceType` value; tier 2 for `platform_record` (authoritative that the ad
  ran), tier 3 for operator-transcribed.
- `dimensions` from `extract.dimensions_of(sentence)`; falls back to new
  `Dimension.MESSAGING` when no existing dimension matches.
- `extraction_certainty`: 0.9 platform record, 0.8 OCR-corroborated, 0.6
  operator-transcribed.

**Step 2 — landing pages.** For distinct normalized destinations (budget-capped
via existing `Budget.max_sources`), fetch through `Collector`; evidence from
`extract_evidence` as for any page, `discovered_by="ad_destination"`.

**Step 3 — `analyze.ad_section()`** (new). Produces findings in three labelled
classes, using existing types:

| Class | Representation | Examples |
|---|---|---|
| Observation | `Finding(basis=DIRECT)`, cites ad evidence | "Freshworks ad 123 headline: 'Switch from Zoho in 7 days'"; "8 of 12 captured ads use CTA 'Sign up'" (count = DERIVED with all evidence ids) |
| Analytical conclusion | `Finding(basis=DERIVED)`, cites every evidence it aggregates | "Price-led offers in 5 of 12 ads; landing page states the same price on 4 (evidence …)"; "Messaging theme 'migration' appears in ads of 2 of 3 competitors" |
| Hypothesis | `Finding(basis=INFERRED, confidence=LOW)` via existing `_inferred()` | "Competitor X may be targeting Zoho users (headlines name Zoho in 3 ads)" — worded as a hypothesis with a test to validate |

Analysis topics → mechanism:
- Product promotion: which product names (`subject_terms`) appear per ad.
- Value propositions / pain points: `dimensions_of` + `extract` patterns over ad
  sentences (PAIN_POINT, COMPETITION, MESSAGING).
- Offers: regex lexicon (free trial, % off, discount, free plan) + `pricing`
  parse for stated prices.
- CTAs / formats: tallies over latest observations.
- Landing-page alignment: (a) ad headline terms present in landing page text
  (`extract.relevance`), (b) ad-stated price/offer re-found on the landing page
  via `pricing`; mismatch is an observation ("ad says ₹999; landing page shows
  ₹1,299"), not a judgement.
- Positioning: per competitor, top dimensions + named rivals in ad text.
- Patterns / change over time: per ad, consecutive observations with
  different `content_sha256` → change finding citing both observations; new ads
  since previous capture; ads not observed in the latest capture → "not
  observed since <date>" only.

**Forbidden inferences (hard rule, enforced by test and gate):** no statement
may estimate, rank or imply spend, budget, impressions, reach, CTR, ROAS,
conversions, targeting, audience, or performance/success. Platform-disclosed
values (`disclosed_reach`, ATC times-shown ranges, Meta EU reach) may appear
**only verbatim** in an observation finding citing that field. Ad longevity is
an observation only ("scheduled since 2026-03-01"); never "long-running
therefore effective".

**Step 4 — validation.**
- `verify.py`: new `ad` locator branch — load `raw_sha256` from rawstore, parse
  the stored capture, assert the excerpt equals the field's verbatim at that
  path.
- `benchmark.py`: new gate `no_performance_inference` — every finding whose
  statement matches the forbidden lexicon must cite only evidence whose
  excerpt contains the matched value verbatim from a `disclosed_reach` field;
  otherwise fail. Existing `unsupported` gate already enforces INFERRED → LOW.
- Existing `citations` and `provenance` gates cover ad evidence once the
  locator branch exists.

**LLM use.** None required. The optional `llm.py` narrative may summarize the
section; its existing citation and number checks apply unchanged. LLM-based
theme classification is out of MVP (D10).

---

## 9. Report integration design

- **Request:** add `ProductInput.include_ads: bool = False` (opt-in; report
  shape unchanged when false). Industry mode: not in MVP.
- **Engine:** in `run_research`, beside the `dossier_evidence` call
  (`engine.py` ~729–740): if `include_ads`, call `ad_evidence` in a thread,
  extend sources/evidence, add an `Issue` (warning) when no advertiser links or
  no observations exist.
- **Sections:** new `competitor_ads` section ("Competitor advertising:
  observed messaging") in `product_sections`. Its `gaps` always state method,
  capture window, count of ads per competitor, and the coverage caveat.
  Selected ad findings may also flow into existing `competitors` and
  `differentiation` sections because they share dimensions.
- **Limitations list** gets the capability-table limitations of every method
  used (e.g. "India commercial ads: manually captured; not exhaustive").
- **Versions:** `SCHEMA_VERSION` 2 → 3 (new enum values `SourceType.AD_LIBRARY`,
  `Dimension.MESSAGING`, locator `ad`), `ANALYSIS_VERSION` bump. API clients
  validating enums (Postman collection, PR #10) need updating.
- **Markdown:** `report.to_markdown` renders the section unchanged.
- **Import entry point:** `gtm-market import-ads <file.json> [--screens DIR]
  --operator NAME` in `runner.py`. API route `POST /v1/market/ads/import` in
  Phase 2 (multipart, size limits, same auth dependencies as existing routes).

No separate research engine, queue, or report type.

---

## 10. Testing and evaluation strategy

**Unit (no network, no DB):**
- `ManualImport` validation: schema, missing platform ID refused, oversized
  image refused, field states required, operator required.
- Normalization: tracker stripping table-test; click-tracker URLs yield
  `not_collected` landing.
- `content_sha256` stable under whitespace/ordering, changes on text change.
- Capability refusal: Meta API adapter refuses `countries=["IN"]` commercial.
- `ad_evidence` claim wording ("states:"), locator format, certainty by
  verification.
- Forbidden-inference lexicon check: positive and negative cases.

**DB-backed (existing Postgres test pattern):**
- Migration up/down; `UNIQUE(platform, platform_ad_id)`; idempotent re-import
  (`UNIQUE(ad_id, import_id)`); no-UPDATE assertion.
- Prune keep-set includes ad digests.

**Adapter fixtures (Phase 3/4):** recorded responses built from the documented
schemas [M5], [G2]; no live calls in CI.

**Evaluation set (MVP exit criterion):**
- 30–50 ads, 3–5 Indian SaaS competitors, captured manually by an operator,
  each with screenshot + transcription; a second person labels themes, offers,
  CTA, format, landing alignment.
- Stored under `benchmarks/market/ads/` as frozen import files + raw.
- Metrics:
  - field-extraction accuracy vs labels (target set from baseline, reported);
  - theme-label agreement (per-label precision/recall; reported, not gated
    until a baseline exists — same policy as `MARKET_BENCHMARK.md`);
  - **gated:** citation validity = 100 %, provenance re-derivation = 100 %,
    forbidden-inference violations = 0, INFERRED findings all LOW.

---

## 11. Security, reliability, and cost risks

| Risk | Mitigation |
|---|---|
| SSRF via ad destination URLs | Existing `netguard` + `HttpFetcher`; destinations are untrusted input |
| Paid-click side effect from tracker URLs | Never fetch click trackers (§6); test |
| Untrusted import files (JSON bombs, image bombs, path traversal in screenshot names) | Size caps, `Strict` schema, `MAX_PIXELS`, basename-only file references, content-type sniffing |
| Prompt injection via ad text | `llm.py` already treats evidence as quoted data and drops uncited sentences |
| Accidental scraping of ad libraries by the crawler | Explicit host deny in `policy.py` (§6) |
| Meta token leakage | Secrets config, never logged; scope per environment |
| Media URL expiry | Capture bytes at collection; else `inaccessible` |
| Operator error / fabrication in manual import | `operator` mandatory, raw import stored, OCR corroboration, `verification` level lowers certainty |
| Stale history misread as "stopped" | "Not observed" wording rule + test |
| Report schema bump breaks clients | Version bump + Postman collection update |
| Cost: storage | Screenshots ~0.1–0.5 MB each; 1,000 ads ≈ ≤0.5 GB in rawstore — negligible |
| Cost: OCR | CPU only, existing Tesseract path, ≤1 image per ad |
| Cost: LLM | Zero in MVP (optional narrative only, already budgeted by `market.llm_model`) |
| Cost: operator time | Dominant cost (~1–2 min per ad). Record in `cost_event`? `COST_KINDS` has no `manual`; defer, report counts instead |
| Cost: Google dataset (Phase 4) | Unknown size; BigQuery free tier 1 TB/month [G2]; measure before building |

---

## 12. Phased implementation plan

| Phase | Content | Exit criterion |
|---|---|---|
| **P0 — decisions** | Product owner answers §13; legal reads Meta terms [M3] and ATC terms for manual capture + storage | Written sign-off on D1–D3 |
| **P1 — MVP** | Migration (`ad`, `ad_observation`); capability table; `ManualImport` + CLI; OCR corroboration; `ad_evidence`; landing-page inspection with tracker stripping; `policy.py` deny-list; `ad_section` (observations, derived tallies, landing alignment, hypotheses LOW); `ad` locator in `verify.py`; `no_performance_inference` gate; schema version bump; eval set of 30–50 ads | All gated metrics pass on the eval set; ruff/mypy/pytest green |
| **P2 — history** | Change-over-time findings (content-hash diffs, new / not-observed ads); API import route; cross-platform clusters at analysis time | Re-import of a second capture produces correct change findings on fixtures |
| **P3 — Meta API (EU/UK)** | `MetaAdLibraryApi` adapter; only if D4 = yes and token/identity confirmation done | Fixture tests; one live EU run documented |
| **P4 — Google dataset (EEA/Turkey metadata)** | `GoogleAtcDataset` adapter after measuring download size/cadence | Same |
| **P5 — gated extras** | Vendor adapter (D2), video transcription (D9), LLM theme labelling (D10) | Each needs its own approval |

Rough MVP size: one migration, ~5–7 touched modules, one new module
(`market/ads.py`: capability table, import, `ad_evidence`), tests and fixtures.
Estimate ~1,000–1,500 lines including tests; not measured.

---

## 13. Decisions requiring product-owner approval

| # | Decision | Recommendation |
|---|---|---|
| D1 | Legal sign-off: humans viewing Meta Ad Library / Google ATC and importing what they see (text, IDs, screenshots) into COIREI | Required before P1 ships |
| D2 | Use third-party scraping vendors (SerpApi, Apify actors) at all | No for now; revisit only with legal review of vendor terms |
| D3 | Storage/retention of screenshots and creative text; use in client deliverables | Internal analysis only; rawstore retention while cited; no media in deliverables |
| D4 | Is EU/UK/EEA competitor ad coverage valuable (gates P3/P4) | Defer until a customer needs it |
| D5 | Base branch: build after PR #11 merges, or on main | After PR #11 (migration numbering only) |
| D6 | Allow manual imports without a platform ad ID | No |
| D7 | Who may run imports; operator identity and audit | Named operators; `operator` field mandatory |
| D8 | May ad longevity appear even as a LOW hypothesis about performance | No — observation only |
| D9 | Video creatives: transcribe or reference only | Reference only |
| D10 | LLM for theme labelling (cost, determinism) | No in MVP; deterministic rules + eval first |
| D11 | Report `SCHEMA_VERSION` bump and new enum values (client impact) | Accept; update Postman collection |
| D12 | Add explicit crawler deny-list for ad-library hosts | Yes (also protects existing crawler today) |
| D13 | Political/issue ads in scope | No |

---

## 14. Non-goals

- Automated collection of India commercial ads from either platform.
- CAPTCHA, login, anti-bot or rate-limit circumvention; proxy rotation;
  browser automation of ad library UIs.
- Spend, impression, reach, ROAS, conversion, targeting or performance
  estimation of any kind.
- Political and issue ads.
- Video transcription, face/person analysis.
- A separate ad-research engine, queue, report type or vector store.
- Monitoring/alerting on new competitor ads (possible after P2, not designed).

---

## Recommendation

**Yes — a small extension of the existing market engine.** The engine already
has the hard parts (guarded fetching, raw storage, OCR, rule-based extraction,
evidence model with DIRECT/DERIVED/INFERRED, verification, deterministic
benchmark gates, identity links). The only new infrastructure is two
append-only tables, justified because existing evidence tables are bound to
crawled pages and report JSON cannot hold cross-run history.

**Minimum viable first milestone (P1), confirmed:** manual import of
competitor ads (JSON + screenshots, platform ID required) → append-only
observations → `ad_evidence` into a product-mode run → landing-page
inspection through the existing `Collector` → a `competitor_ads` report
section of observations, derived tallies and LOW hypotheses → verified by
the existing gates plus `no_performance_inference`, on a 30–50-ad Indian SaaS
evaluation set. No platform API, no vendor, no LLM required. Start only after
D1 (legal) is signed off.
