# Held-out pricing set (heldout-v1)

**Status: development data since 2026-10-23.** v1 was scored once, on
f7b0dce and on 52e8bc9 (`results/`). Its errors then guided the recall work
that followed, so scores of later code on v1 are development scores. The
held-out set for that code is `../heldout_v2/`.

A labelled set of 32 web pages for measuring price extraction on pages that the
extractor's developers have not seen. It is **held out**. Do not use it to tune
rules, thresholds or prompts. Score a version against it once, and report that
score. Once the set has been scored and used to guide changes, it counts as
development data for later versions, and a new held-out set is needed.

## How it was built

Labeller A built and labelled the set on 2026-10-21 without seeing the extractor.
They did not read the extractor code, its tests, the other pricing cases, or any
earlier results, and they ran no extractor. The labelling guide is
`../README.md` (field meanings, `pricing_type` values, the "not prices" rules)
plus the extra fields listed below.

* **22 live snapshots** (`origin: live_snapshot`). Each is a public page fetched
  on 2026-10-21 with curl: one GET, a normal desktop browser User-Agent,
  redirects followed, a 20 s timeout, and only after robots.txt allowed the
  path. A page was kept only if its raw HTML, with no JavaScript run, shows the
  prices in visible text or JSON-LD. `original_sha256` is the hash of the bytes
  curl received. `url` is the final URL after redirects, and `requested_url` is
  given when it differs.
* **10 synthetic pages** (`origin: synthetic`, ids `syn_*`). Labeller A wrote
  these as realistic HTML for situations that are hard to find live: amounts
  split across spans, billing toggles whose state is not in the HTML, add-ons
  repeated on each plan card, setup fees, intro prices, struck-through prices,
  JSON-LD offers, ambiguous amounts in prose, and non-price numbers near prices.
  Every company on these pages is fictional, and the URLs use the reserved
  `.example` domain.

The following hosts and vendors were excluded because the system was built
with them: the development hosts, plus EmailOctopus, Brevo, Kit/ConvertKit,
Moosend, Mailjet, Zoho, Wave, FreshBooks, Mailchimp, Klaviyo, Invoicera, rvo.nl
and gfi.org.

### Snapshot reduction (live pages)

The fetched HTML was parsed with lxml (decoded as UTF-8 when valid) and
reduced as follows:

* Removed: `<script>` elements other than `type="application/ld+json"`,
  `<style>`, `<svg>`, `<noscript>`, `<iframe>`, HTML comments and inline
  `style` attributes. Tail text was kept.
* Everything else was kept, including the head `<title>`, the structure,
  classes and data attributes.

The reduced snapshots of third-party pages are committed so the score can be
reproduced. The repository is private; do not publish them. The reduced
document is stored as `pages/<id>.html.gz` (gzip, mtime 0).
`snapshot_sha256` is the hash of the uncompressed reduced HTML. Synthetic pages
are stored the same way, as full documents. The pages were frozen before the
labels were written and have not changed since.

## Files

* `manifest.json`: `{"version", "created", "pages": [...]}`. Each page has `id`,
  `url`, `title`, `vendor`, `page_kind` (vendor_pricing | vendor_other |
  comparison | review | blog | market_report | marketplace | shop), `origin`,
  `industry`, `geography` (the country or region the page is about, or null),
  `fetched_at` / `http_status` / `original_sha256` (live pages only),
  `snapshot_sha256` and `needs_render` (always false).
  `vendor` names the company whose own offering the page prices. It is null for
  blogs, reviews and guides that price other companies' products, and for the
  marketplace page, where sellers make the offers.
* `labels_a.json`: `{"labeller": "A", "pages": {"<id>": {"expected": [...],
  "not_prices": [...], "notes": "..."}}}`. The per-page `notes` explain each
  judgement call.
* `labels_b.json`: the same pages, labelled independently by labeller B (same
  guide; B did not see A's labels, the extractor or any results).
* `disagreements.json`: from `agree.py`. B's prices are paired with A's by
  amount. The file lists every field the pair disagrees on and every unpaired
  price.
* `labels.json`: **the scored labels.** An adjudicator, a third agent that also
  never saw the extractor, its tests or any results, decided each
  disagreement from the page and the guide.
* `adjudication.json`: the adjudicator's conventions, counts and one decision
  per disagreement, each with a reason.
* `human_review_sample.json`: 40 final prices and 10 not-prices, drawn with
  seed 20261022 across all pages and 9 pricing types, for a human reviewer.
  `decision` is null until a reviewer fills it in.
* `results/<version>__<code>.json`: one score per (dataset version, code),
  written by `score.py heldout --code <commit>`. The scorer refuses to
  overwrite a result and checks every snapshot's hash first.

## Provenance and agreement (heldout-v1)

All three labellers are AI agents. No human has reviewed the labels yet; see
`human_review_sample.json`.

| Step | Count |
|---|---|
| Labeller A | 587 prices, 180 not-prices |
| Labeller B | 585 prices, 157 not-prices |
| Paired by amount (`agree.py`) | 581, of which 227 are identical on every field; 6 only in A, 4 only in B |
| Most disputed fields (pairs) | product 115, basis 101, included 97, tax 74, plan 70, effective 39 |
| Adjudicated paired fields | 561: A 237, B 323, other 1 (re-paired by evidence text) |
| Unpaired prices | A: 4 kept, 3 dropped; B: 3 kept, 2 dropped |
| Price vs not-price conflicts | 3, all ruled not-prices |
| Final `labels.json` | 587 prices, 203 not-prices, 3 unjudgeable fields on 3 records |

The disagreement rate is itself a result. Plan, product, basis, inclusions
and tax are where careful readers differ, so scores on those fields carry
label noise of the same order. One judgement call is worth knowing: the
fast2sms page's "0.25 paisa" was ruled ₹0.25, not ₹0.0025, because the
page's own tier list gives ₹0.25/SMS for the same offers.

## Label fields added to the guide

An expected price has every field in `../README.md`, plus:

| Field | Meaning |
|---|---|
| `evidence_text` | A verbatim snippet of the visible text (at most 120 characters) that contains the amount. It matches the page's visible text with whitespace collapsed, with block elements joined by a space. JSON-LD evidence quotes the JSON. |
| `layout` | card, table, inline, list, json_ld or other |
| `attribution` | `clear`: the page makes plain which plan or product the amount belongs to. `none`: there is no plan or product name to attribute, as for a vendor's single offer or tiers named only by size. `ambiguous`: a careful reader cannot tell, and plan and product are null. |
| `unjudgeable` | The fields that cannot be decided from the snapshot. One example is `["billed"]` when a monthly/annual toggle's state is not in the HTML. `["*"]` means the whole price cannot be judged. Normally `[]`. |
| `plan` / `product` | The plan or tier as the page names it. The product is the add-on, goods item, tool on a comparison page, or report title on a report page. On the marketplace page, `plan` holds the seller whose offer it is. |

Every visible occurrence is labelled. An amount repeated in a table, on a card
and in the FAQ gives one entry per occurrence. So do duplicate tab panels and
desktop/mobile menus that are present in the HTML. When the HTML holds both the
monthly and the annual variant, both are labelled. Amounts in cents, pence and
paise are converted to the main unit (50¢ is 0.50 USD, 11 paise is 0.11 INR).

A not-price has `amount`, `why` and `evidence_text`. `why` is one of savings,
discount, credit, gift_card, spend_threshold, refund, revenue, funding,
market_size, investment, salary, count, rating, year, percentage_not_fee,
competitor_average, report_price_off_topic or other. The not-price lists are
representative, not exhaustive. They cover the money amounts and percentages
next to prices, and the counts, ratings and years that an extractor could
mistake for prices.

## Pages tried and skipped (2026-10-21)

| Host | Reason |
|---|---|
| mollie.com | over 200 amounts on one page, too many for the size of this set |
| backblaze.com | robots.txt disallows the path |
| razorpay.com | the prices are rendered by JavaScript |
| aussiebroadband.com.au, vodien.com, open.ac.uk, ox.ac.uk | HTTP 403 |
| clicksend.com | redirected to an Indian-region variant that did not match the requested page |
| tallysolutions.com, hostinger.com/sg | HTTP 404 |
| pinboard.in | HTTP 503 |
| superbutcher.com.au (first product URL) | HTTP 404; another product page was used instead |
| nus.edu.sg | the HTML has no content without JavaScript |
| giffgaff.com | the HTML has no content without JavaScript |
| lucintel.com | robots.txt disallows the path |
| milessmithfarm.com | no prices in the HTML |
| ubc.ca | tuition amounts not in the HTML (rendered by JavaScript) |
| messagecentral.com | country rate table without column headers (JS calculator); too many unattributable numbers |
| digitalocean.com, twilio.com, stripe.com (gb, au), cashfree.com, unsw.edu.au | 80 to 1,000 amounts each, too many for the size of this set |
| iot-analytics.com | fetched; not used (a market-report page was already covered) |
