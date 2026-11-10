# Pricing benchmark

Labelled cases for price extraction (`docs/MARKET_BENCHMARK.md`, "Pricing").
Labels are written from the text alone, by a labeller who has not seen the
extractor, and say what a careful analyst reads the text to state, not what
any rule would produce.

* `cases.json`: fixture cases (HTML written for the benchmark), one or more
  pages each.
* `live.json`: amounts in sentences of pages collected by the live benchmark
  runs 6 and 7 (stored raw pages), labelled the same way.
* `score.py`: runs the extractor on the cases and scores it.

## A case

```json
{
  "id": "sub-setup-01",
  "category": "subscription vs setup fee",
  "pages": [
    {
      "url": "https://acme-invoicing.com/pricing",
      "title": "Pricing - Acme Invoicing",
      "vendor": "Acme Invoicing",
      "html": "<h2>Pro</h2><p>The Pro plan costs $29 per user per month. A one-time $99 setup fee applies.</p>",
      "expected": [ { "...": "one entry per price (see below)" } ],
      "not_prices": [ { "amount": 100, "why": "savings" } ],
      "needs_render": false
    }
  ],
  "geography": null,
  "expect_conflict": false,
  "note": "why the case exists"
}
```

`html` is the page's `<body>` content (headings, paragraphs, lists,
tables, `<script type="application/ld+json">`). `vendor` is the company
whose page it is when the page is a known competitor's own page, else
null. `geography` is the research request's geography (null = none).

## An expected price

| Field | Values | Meaning |
|---|---|---|
| `amount` | number or null | The amount as written (2.9 for "2.9%", 1500 for "$1.5k"); null only for `quote` |
| `currency` | ISO code or null | What the text or the page establishes. Bare `$` with nothing suggesting another dollar: "USD" with `currency_assumed: true`. Bare `$` on a page about Singapore, Hong Kong, Australia, Canada, New Zealand etc. without a code: null. Null for percentages |
| `currency_assumed` | bool | true when "USD" is read from a bare `$` |
| `percent` | bool | The amount is a percentage ("2.9% per transaction") |
| `pricing_type` | see below | What the amount pays for |
| `period` | month, year, week, day, hour, null | The period the amount is charged per, as stated ("$25/month" = month even if billed annually). Null when not stated: never inferred |
| `billed` | monthly, annually, quarterly, null | A billing commitment stated separately ("billed annually", "annual plan", "paid yearly") |
| `basis` | string or null | What the amount is per besides time: "user", "seat", "transaction", "lb", "API calls", "contact", "GB", "message", "employee", "order", "invoice", "location", ... (singular, lower case) |
| `basis_quantity` | number or null | "per 1,000 API calls" = 1000; null when 1 |
| `bound` | exact, from, up_to, about | "starts at / from / as low as" = from; "up to / max" = up_to; "about / around / ~" = about |
| `plan` | string or null | The plan, tier or package the amount is for ("Pro", "Business"), from the sentence, heading, or table column/row; null when none is named |
| `product` | string or null | The product priced when the text names one distinct from the vendor (an add-on's name, a goods item) |
| `promotional` | bool | Introductory, limited-time, first-N-months or discounted price |
| `tax` | included, excluded, null | Only when stated ("excl. VAT", "+ GST", "incl. tax") |
| `included` | string or null | A stated allowance of the price ("500 contacts", "10,000 emails/month") |
| `effective` | string or null | A date the text says the price applies from or as of ("2019", "March 2025") |

`pricing_type`:

| Value | For |
|---|---|
| `subscription` | A recurring charge for the product or a plan (per month/year, per user per month, a free $0 plan) |
| `one_time` | A one-time purchase of the product (a licence, a device, a report) |
| `setup` | Setup, implementation, onboarding, installation fees |
| `transaction` | A fee per payment, order or transaction, fixed or percentage |
| `usage` | A metered charge: per N API calls, GB, messages, minutes, overage |
| `unit` | Goods priced per unit of quantity (per lb, per kg, per item, per pack) |
| `add_on` | An optional extra bought on top of a plan |
| `minimum` | A minimum charge ("minimum monthly fee of $10") |
| `quote` | Custom / contact sales / "request a quote": no amount |
| `unknown` | An amount stated as a price whose role the text does not establish |

## Not prices

`not_prices` lists amounts in the text that are not a price of the product,
with `why`: savings ("save $100"), discount amounts ("$20 off"), credits
("$200 in credits"), gift cards, minimum-spend or free-shipping thresholds
("spend at least $500", "free shipping over $50"), refunds, cash back,
salaries, revenue, funding, market sizes, investment or capex, fines,
the price of a different thing (a competitor's average, a report's own
price when the page is not about reports), plain counts with `$` in a name.
Percentages that are not fees (growth, discounts "20% off", shares) are
`not_prices` too.

A JavaScript-rendered pricing page whose HTML states no amount has
`expected: []` and `needs_render: true`.

## Scoring

`score.py` (its docstring has the matching rules) scores the code on
`PYTHONPATH`: `fixtures`, or `live --raw <dir>` with the live runs' raw stores
(`bench6/`, `bench7/`, not committed). The 2026-10-21 scores of the code
before price terms (2b188b8) and after are in `results/`
(`old_fix.json`, `new_fix.json`, `old_live.json`, `new_live.json`) and in
`docs/MARKET_BENCHMARK.md` ("Pricing").
