# Market sizing methodology (TAM / SAM / SOM)

Implementation: `gtm_crawler/market/sizing.py`. All arithmetic is in code;
no model is ever asked to calculate.

## Definitions

* **TAM**: total annual demand under the stated `market_definition`,
  geography, segment, year and currency.
* **SAM**: the part of TAM the product can serve: TAM x each SAM filter
  (a fraction for geography, segment, capability, channel...).
* **SOM**: the part of SAM obtainable within the horizon: SAM x an
  obtainable share (top-down) or penetration (bottom-up).

SAM is not reported without at least one SAM filter, and SOM not without a
share: they are missing, never silently equal to the level above.

## Methods

**Top-down**: `TAM = market_total`; `SAM = TAM x f1 x f2 ...`;
`SOM = SAM x obtainable_share`.

**Bottom-up**: `TAM = customers x units_per_customer x price_per_unit`
(per year); SAM and SOM as above with `penetration`.

When both are given, `cross_check` reports the ratio of their base TAMs
and flags a difference over 2x ("the market definitions or inputs disagree").

When no top-down input is given and `use_evidence_total` is true, the
top-down total comes from collected `market_size` figures of the **target
scope** (`sizing.target_scope`): the requested geography (Global when none
is requested), the requested segment (the whole market when none), the
request's currency, the latest year not after `year`, measure "market", not
a part of a larger figure, not another market, and no qualifier outside the
sizing's `market_definition` (audit M7, `docs/MARKET_ENGINE_IMPLEMENTATION.md`
"Market scope"). Every other collected market-size figure is listed in
`sizing.excluded` with its reason (another geography or segment, currency,
later year, spending or investment, a part, another or a qualified market,
one source stating several values, or "not selected" for an older eligible
year). A figure whose geography is not stated is used only when no geography
was requested and no figure states Global, and then with a warning and never
verified; a figure older than `year` is noted as not adjusted for growth.
No currency or year conversion is made. Of the eligible figures: range = the figures' spread,
base = the midpoint of that range, basis sourced with their evidence ids. It is marked
**not independently verified** unless that figure's confidence is HIGH and it
is uncontradicted, and it warns that sources' market definitions may
differ from the request's.

## Inputs

Each input is a `Quantity`: `name`, `base`, optional `low`/`high`
(`low <= base <= high`), `unit`, `basis`:

* `sourced`: requires `evidence_ids`;
* `assumption`: requires a `rationale`;
* `derived`: calculated here.

Filters and shares must have unit `fraction` and lie in [0, 1]. Each filter
names the `dimension` it narrows; two filters on the same dimension are
refused as double counting. Money inputs must be in the request currency
(no conversion); counts may not be money. The bottom-up price is per year:
a unit naming another period (`USD/seat/month`, `/week`, `/day`, ...) is
refused (multiply it out first), and a unit naming no period (`USD/seat`)
is warned "assumed per year". In a research run, a sourced input whose
`evidence_ids` are not evidence of that run is warned "cites evidence not
collected in this run" and is never verified.

## Scenarios and sensitivity

* **low / base / high**: every input at its low, base or high value at once.
  These are bounds of the stated ranges, not probabilities or confidence
  intervals.
* **sensitivity**: one input at a time to its low and high end, others at
  base; rows sorted by the swing of the deepest level calculated (SOM, else
  SAM, else TAM). Inputs without a range are omitted.

## Verification

`verified` is true only when every input is sourced and each input's
evidence is corroborated: at least two independent domains and no
contradiction. Otherwise the warnings name the assumptions and the
uncorroborated sourced inputs. A sizing with `verified: false` is an
estimate with its basis shown, not a validated number.

## When evidence is insufficient

`SizingReport.insufficient` lists each method that could not run and why
(no inputs; no collected figure in this currency and year; a refused input).
The API endpoint `POST /v1/market/sizing` sizes from explicit inputs alone;
nothing there counts as corroborated.

## Worked example (tested: `test_top_down_sizing_arithmetic_and_scenarios`)

Market USD 1.0M (0.8-1.2M), SMB share 0.5 (0.4-0.6), obtainable share 0.1
(0.05-0.2):

| | TAM | SAM | SOM |
|---|---|---|---|
| low | 800,000 | 320,000 | 16,000 |
| base | 1,000,000 | 500,000 | 50,000 |
| high | 1,200,000 | 720,000 | 144,000 |

Sensitivity: the obtainable share moves SOM most (25,000 to 100,000).
