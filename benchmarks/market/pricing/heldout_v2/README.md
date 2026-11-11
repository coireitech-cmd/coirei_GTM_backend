# Held-out pricing set (heldout-v2)

A labelled set of 32 web pages for measuring price extraction on pages that the
extractor's developers have not seen. It is **held out**. Do not use it to tune
rules, thresholds or prompts. Score a version against it once, and report that
score. Once the set has been scored and used to guide changes, it counts as
development data for later versions, and a new held-out set is needed.

It was built the same way as heldout-v1 (`../heldout/README.md`), with new hosts,
vendors, industries and currencies.

**Status:** scored once on 2026-10-23, on f7b0dce and on 7fbbdf0 (`results/`).
It has not been used to change any rule. The first change made in response
to its errors makes it development data.

## How it was built

Labeller A built and labelled the set on 2026-10-23 without seeing the extractor.
They did not read the extractor code, its tests, the other pricing cases, the v1
labels or any results, and they ran no extractor. The labelling guide is
`../README.md` (field meanings, `pricing_type` values, the "not prices" rules),
plus the extra label fields of `../heldout/README.md` and the label conventions
settled for v1 (the `conventions` list in `../heldout/adjudication.json`).

* **22 live snapshots** (`origin: live_snapshot`). Each is a public page fetched
  on 2026-10-23 between 12:05 and 12:17 UTC with curl: one GET, a normal desktop
  browser User-Agent, redirects followed, a 20 s timeout, and only after
  robots.txt allowed the path. A page was kept only if its raw HTML, with no
  JavaScript run, shows the prices in visible text or JSON-LD, and if it has no
  more than about 80 amounts. `original_sha256` is the hash of the bytes curl
  received. `url` is the final URL after redirects, and `requested_url` is given
  when it differs.
* **10 synthetic pages** (`origin: synthetic`, ids `syn2_*`). Labeller A wrote
  these as realistic HTML (nested divs and spans, amounts split across spans,
  plan names in their own elements, tables, add-on lists, FAQ text with numbers)
  for situations that are hard to find live: a billing toggle whose state is not
  in the HTML, add-ons next to plans, setup and one-time fees, percentage plus
  fixed transaction fees and per-transaction minimums, intro and struck-through
  prices, metered rate tables and minimum charges, goods priced per kg or dozen
  on a page whose bare `$` names no currency, a report page with licence prices
  and market sizes, a review blog with prose and table prices and an amount that
  does not say what it pays for, JSON-LD offers (including an AggregateOffer),
  quote-only plans, and non-price numbers next to prices. Every company on these
  pages is fictional, and the URLs use the reserved `.example` domain.

Mix: 21 industries (insurance, utilities/energy, logistics and postal, events
and attractions, legal and accounting services, gyms, coworking, language
education, government fees, payments, hosting/CDN, research reports, food,
SaaS); 13 currencies (GBP, EUR, USD, CHF, SEK, BRL, PLN, MXN, JPY, COP, ZAR,
CAD, AUD); pages about the UK, Spain, Germany, Portugal, the Netherlands,
Switzerland, Sweden, Poland, Mexico, Brazil, Colombia, the US, Canada, New
Zealand, South Africa and Japan.

The following hosts and vendors were excluded: the development hosts and vendors
(as for v1: EmailOctopus, Brevo, Kit/ConvertKit, Moosend, Mailjet, Zoho, Wave,
FreshBooks, Mailchimp, Klaviyo, Invoicera, rvo.nl, gfi.org and their hosts), and
every host and vendor of heldout-v1.

### Snapshot reduction (live pages)

The same rule as v1. The fetched HTML was parsed with lxml (decoded as UTF-8
when valid) and reduced as follows:

* Removed: `<script>` elements other than `type="application/ld+json"`,
  `<style>`, `<svg>`, `<noscript>`, `<iframe>`, HTML comments and inline
  `style` attributes. Tail text was kept.
* Everything else was kept, including the head `<title>`, the structure,
  classes and data attributes.

The reduced snapshots of third-party pages are committed so the score can be
reproduced. The repository is private; do not publish them. The reduced
document is stored as `pages/<id>.html.gz` (gzip, mtime 0).
`snapshot_sha256` is the hash of the uncompressed reduced HTML. Synthetic pages
went through the same reduction and are stored the same way, as full documents.
The pages were frozen before the labels were written and have not changed since.

## Files

* `manifest.json`: `{"version", "created", "pages": [...]}`, with the page fields
  of v1: `id`, `url`, `title`, `vendor`, `page_kind`, `origin`, `industry`,
  `geography`, `fetched_at` / `http_status` / `original_sha256` (live pages
  only), `requested_url` (when redirected), `snapshot_sha256` and
  `needs_render` (always false). `vendor` is null for the review pages and the
  comparison site, which price other companies' offers.
* `labels_a.json`: `{"labeller": "A", "pages": {"<id>": {"expected": [...],
  "not_prices": [...], "notes": "..."}}}`, with the label fields of v1
  (`evidence_text`, `layout`, `attribution`, `unjudgeable`, `plan`, `product`
  and the guide's fields). The per-page `notes` explain each judgement call.

* `labels_b.json`: the same pages, labelled independently by labeller B. B saw
  neither A's labels nor the extractor.
* `disagreements.json`: written by `../heldout/agree.py heldout_v2`.
* `labels.json`: **the scored labels.** A third agent, also blind to the
  extractor, adjudicated A and B against the page.
* `adjudication.json`: v1's 25 conventions, 16 new ones, the counts, and one
  decision per disagreement, each with a reason.
* `human_review_sample.json`: 40 prices and 10 not-prices, drawn with seed
  20261023, for a human reviewer. `decision` is null until reviewed.
* `results/heldout-v2__<commit>.json`: one score per code version.

## Provenance and agreement (heldout-v2)

All three labellers are AI agents, and no human has reviewed the labels yet.
v2 started from v1's adjudicated conventions; v1 had no such starting point.

| Step | Count |
|---|---|
| Labeller A | 548 prices, 200 not-prices |
| Labeller B | 596 prices, 186 not-prices |
| Paired by amount | 543, of which 331 are identical on every field; 5 only in A, 53 only in B |
| Most disputed fields | plan 95, product 72, basis 38, included 31, attribution 30 |
| Adjudicated paired fields | 336: A 78, B 258 |
| Unpaired prices | A: 2 kept, 3 dropped; B: 1 kept, 52 dropped |
| Price vs not-price conflicts | 32, all ruled not-prices |
| Final `labels.json` | 546 prices, 251 not-prices, 22 unjudgeable fields on 22 records |

What B labelled and the adjudication dropped:
- **quotezone_pet:** the breed table's average premiums, ruled market averages.
- **crunch:** the "worth £X" values.
- **Invisible amounts:** amounts held only in `<template>` content or data
  attributes, which a visitor never sees.

Frozen and committed before any scoring of this set.

Every `evidence_text` was checked to occur in its page's visible text
(whitespace collapsed, block elements joined by a space) or in its JSON-LD
(whitespace collapsed; JSON-LD evidence quotes the JSON as written, escapes
included).

## Labelling notes (heldout-v2, labeller A)

Every visible occurrence is labelled, as in v1: duplicate panels, menus,
FAQ restatements and JSON-LD copies each get their own entry. Choices that recur
across pages, beyond the v1 conventions:

* "Free" standing in a price slot (a fee cell, a plan's price line) is labelled
  as amount 0; mentions that a free plan exists are not.
* Call-to-action buttons ("Contact sales", "Get a quote") on priced cards are
  not labelled; an offer or plan whose only price is a quote is.
* Outputs of a price calculator or quote builder that JavaScript fills in
  (placeholder `0.00` values) are labelled with `unjudgeable: ["*"]`; template
  placeholders that are not amounts are not labelled.
* A JSON-LD Offer gets one entry for its price; a `UnitPriceSpecification`
  inside it restating the same price gives the period. Prices written in JSON-LD
  descriptions or FAQ answers get their own entries.
* A net price written next to "(£X inc VAT)" is read as excluding VAT.
* Instalments of a one-off course price are `one_time` with period month.

## Counts (labeller A)

| | Count |
|---|---|
| Pages | 32 (22 live, 10 synthetic) |
| Expected prices | 548 (410 on live pages, 138 on synthetic pages) |
| Not-prices | 200 |
| Unjudgeable fields | 17 on 17 records (`billed` 10, `*` 7) |

## Pages tried and skipped (2026-10-23)

robots.txt allowed every path that was requested.

| Host | Reason |
|---|---|
| payhip.com, lemonade.com, bupa.co.uk, rocketlawyer.com, alliedmarketresearch.com, inpost.pl, hcsolicitors.co.uk | HTTP 403 (bot challenge) |
| puregym.com | HTTP 404 on the memberships URL, HTTP 403 on a club join page |
| so.energy, ethz.ch, thegymgroup.com, basic-fit.com, clip.mx, vangoghmuseum.nl, expanish.com | HTTP 404 |
| sbb.ch | connection timed out (20 s) |
| sendle.com | the HTML has no content without JavaScript |
| mercadopago.com.mx | the fee amounts are not in the HTML |
| selectra.co.uk, electrickiwi.co.nz | tariff unit rates not in the HTML (few amounts) |
| ofgem.gov.uk | redirected to an explainer page with no prices |
| gov.uk (Companies House fees), nuvemshop.com.br, cervantes.to, hodgkinsons.co.uk, cuantomecuesta.com | about 100 to 260 amounts each, too many for the size of this set |
| uva.nl | fetched (77 amounts); not used, near the cap and education was covered |
| waggel.co.uk, canter-law.co.uk | fetched; not used (insurance and legal services were covered) |
