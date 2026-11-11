# Market research: running the first paid pilots

How market-research reports are produced and delivered during the pilots, and
what the engine does and does not guarantee. Written 2026-10-23.

## Access model: operator-run

There is no customer identity and no per-customer ownership of runs. The API
has one bearer token (`api.token`, rotated with `api.previous_token`; `prod`
refuses to start without it) and every `/v1` route requires it; only the
probes and `/metrics` (counts, no content) are open. Anyone holding the
token can read every run, its evidence and its report.

So the pilots are **operator-run**:

* Customers never receive the token or the API address.
* The operator creates runs (`POST /v1/market/research` with an
  `Idempotency-Key`, or `gtm-market request.json --save`), verifies them
  (below) and exports the report (`GET .../report?format=markdown` or JSON).
* Reports go to the customer through the operator's own secure channel
  (shared drive or e-mail with access control). A run id is never sent as a
  way to fetch a report.

Giving customers direct access needs, at least: a customer identity per
token, an owner column on `market_research`, and every market route
(status, report, ask, cancel) filtered by owner, with cross-customer tests.
That is not built.

## Creating a run without paying twice

`POST /v1/market/research` takes an `Idempotency-Key` header (1-200
characters). The key is stored on the run (`market_research.idempotency_key`,
unique):

| Request | Answer |
|---|---|
| new key | 201, a new run, `created: true` |
| same key, same request | 200, the run created earlier, `created: false` (also when the queue is full, after a restart, or from another API process) |
| same key, another request | 409 naming the run that holds the key |
| no key | 201, always a new run (old behaviour) |

"Same request" means every field the client sent (after defaults such as the
depth's budget are filled in); the server's `research_id` and `created_at`
are not compared. Concurrent creates with one key make one run (the unique
index decides). Keys never expire and are global, which is right for one
operator; with several customers they would have to be scoped per customer.
Use one key per ordered report (an order or invoice number).

## Verifying a report before delivery

```
gtm-market --verify mr_0123456789ab --config prod.toml
```

re-checks every evidence record of the stored report against the stored
artifacts it names by hash (`market/verify.py`: an HTML page is re-parsed
from its raw bytes, a PDF re-extracted from the stored file), prints a JSON
summary and stores it in `market_research.verification` (shown as
`verification` on `GET /v1/market/research/{id}`). The report itself is never
changed.

| Verdict | Exit | Meaning |
|---|---|---|
| `verified` | 0 | every evidence record was checked, and every quote is where its record says |
| `failed` | 1 | at least one quote is not at its location (`mismatch`) or its source changed; do not deliver |
| `incomplete` | 3 | some checks could not run: an artifact is missing or expired (`missing_artifacts` lists the URLs), or evidence has a locator this check does not cover (OCR image, crawled dossier, none: `unchecked`), or the run has no report |

`problems` lists each record that is not verified, with its status, why and
its URL. `incomplete` is not a pass: deliver only after reading the
problems, and say in the report which claims were not re-checked.

## Evidence retention

`gtm-crawl --prune` deletes raw documents older than `rawstore.retention_days`
(90) unless something cites them. Until 2026-10-23 only the crawler's evidence
counted, so the pages and PDFs a market report cites were deleted after 90
days and the report could no longer be verified. They are now kept as long
as the run's row exists (`MarketStore.cited_digests`): the same "kept while
cited" rule the approved retention policy applies to crawled evidence.
Pages a run fetched but did not cite are still pruned.

Open decision (product/legal, not engineering): how long delivered reports
and their sources are kept, and whether a customer can ask for deletion.
Deleting a run's row today releases its artifacts at the next prune.

## Worker restarts and retries

* A worker that stops cleanly (deploy, SIGTERM) hands its run or crawl job
  back without using an attempt, so deploys cannot exhaust `MAX_ATTEMPTS` (3)
  / `worker.max_job_attempts`.
* A worker that dies, hangs past its lease or loses the database uses an
  attempt; after the cap the run is FAILED (crawl jobs are also
  dead-lettered).
* A run cancelled while its worker was dead is closed as CANCELLED when it
  is next claimed, not re-run.
* A database error while storing the outcome (market or crawl) or handing a
  job back no longer stops the worker: the lease expires and another worker
  retries.
* A run whose analysis raises is FAILED but keeps its sources and evidence
  in a retrievable report, with an `analysis` issue.

## Known limits of the figures

* **Market sizes written in other locales are skipped**, not read:
  `4,5 Mrd. €`, `R$ 2,3 bilhões`, `₹1,23,456 crore`, `CHF 2'500 million`
  give no figure (they used to give the tail of the number, e.g. "EUR 5
  million" for "1 234,5 million EUR"). The locale work for prices
  (`market/pricing.py`, PR #6) does **not** cover market sizes; extending
  `extract.parse_metrics` should reuse its number reader once merged, with
  tests per format (grouping, decimal separator, magnitude word, currency).
* **A company's valuation, funding, deal value, market cap, ARR or revenue is
  not a market size** (`extract._company_money`). The rule reads the words
  around the amount; a valuation phrased in a way it does not know can still
  slip through, and a market figure phrased like a company's can be missed.
  Only the benchmark below can say how often.
* **Prices** are compared only in identical terms (type, currency, period,
  billing, basis, tax); anything else is listed as not compared, with why.
* **Competitor coverage** is stated in the report: known competitors given,
  collected, priced, and those not collected with why.
* **Opportunity scores** with unrated dimensions are marked provisional in
  the export, with the unrated dimensions and the basis of every rating.

## Market-size benchmark (plan; nothing measured yet)

No market-size accuracy may be claimed until this exists. The pricing sets
heldout-v1 and heldout-v2 were labelled by AI labellers and have since been
used for development; they are not unbiased estimates. A held-out pricing
v3 needs labels independently checked by a person before its score supports
a claim (`benchmarks/market/pricing/score.py heldout --set-dir
benchmarks/market/pricing/heldout_v3 --code <sha>`, scored once).

A small market-size set (about 60 sentences or table rows, from 30+ pages,
frozen before scoring, labelled by a person and checked by a second):

1. Each item: the source URL and quote; the figure as the source states it;
   value, currency, magnitude, locale of the number; market definition,
   geography, year (or forecast year); and whether it is a market size at
   all.
2. At least a third negatives: company valuations, funding rounds, revenue,
   market caps, report prices, unrelated amounts.
3. At least a quarter locale-written figures (decimal comma, apostrophe and
   space grouping, Indian grouping, `Mrd`, `bilhões`, `crore`).
4. Score: precision and recall of market-size figures, value exact within
   rounding, currency and year exact, scope (definition/geography) agreement;
   negatives wrongly read as market sizes counted separately.
5. Kept under `benchmarks/market/sizing/`, scored once per (set, code) like
   the pricing held-out sets; development uses a separate set.
