# Evidence and verification

How a value gets into a dossier. Detail per component is in the README; this
page is the chain and its checks.

    artifact (raw bytes, SHA-256) -> fetch -> page -> evidence (quote + locator)
        -> observation (what the quote says) -> field_assessment (state)

No evidence, no observation; no supported observation, the field is UNKNOWN.

## Checks before an observation is stored (`db.evidence.check_draft`)

| Check | Refuses |
|---|---|
| quote resolves at its locator on the stored page | invented or moved quotes |
| stated value is inside the quote | values the page does not contain |
| qualifier kept (`_drops_qualifier`) | "500 customers" from "more than 500 customers"; "$49 per month" from "plans start at $49 per month" |
| asserted (`_not_asserted`) | a value that follows a negation or a plan word in its sentence: "exploring enterprise expansion", "do not have", "no longer", "plans to", "will" |
| normalized value carries a qualifier (`eq`, `gt`, `gte`, ...) | bare numbers |
| related organization is named in the quote | relationships the quote does not state |

A refused draft is reported in the extraction statistics (`rejected`) and
never stored. Extraction is rule-based; there is no model in the path, so
page text is data and cannot instruct anything (gap G-13 covers an LLM layer).

## Whose page it is

First-party means the page is on the crawled company's registrable domain.
A page on any other site is read only when identity evidence (a profile
link, or a link to or from the company's site) ties it to the company ([ENTITY_RESOLUTION.md](ENTITY_RESOLUTION.md)).

## States (`assess.py`)

UNKNOWN, SUPPORTED, CORROBORATED, CONFLICTING, STALE, UNSUPPORTED. Confidence
is stored next to the state and never changes it. Stated and derived
observations are kept apart (`observation.kind`).

## Limits

- `_not_asserted` is a word list over the text before the value in its
  sentence; a hedge after the value is not seen.
- Hypotheses are not modelled: there is no reasoning layer (gap G-16).
