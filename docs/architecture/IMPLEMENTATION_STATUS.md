# Implementation status

Branch `feat/identity-resolution` (base `acba095`), 2026-10-12. Nothing is
committed yet. Gaps: [IMPLEMENTATION_GAP_REGISTER.md](IMPLEMENTATION_GAP_REGISTER.md).

## Current phase

Phase 1 (identity and entity resolution): done. Evidence alignment (part of
Phase 5) strengthened. Phases 2-3 (source adapters, per-source policy) not
started.

## Iterations

| # | Objective | Result |
|---|---|---|
| 1 | Audit and baseline | 856 passed, 9 skipped; ruff and mypy clean; gap register written |
| 2 | Identity normalization (G-06) | `gtm_crawler/identity.py`, `tests/test_identity.py` |
| 3 | Named organizations: per-namer rows, links, decisions, review (G-01, G-02, G-03, G-07) | `db/identity.py`, migration 0015, dossier follows MATCH links |
| 4 | Profiles as external identities (G-04) | `resolve_profiles`: reciprocal MATCH, one-way PROBABLE_MATCH, two claimants CONFLICTING |
| 8 | Several reciprocal profiles of one platform are held for review (G-22); three extraction errors from the live runs (G-24) | verified by re-extracting the stored itechpanda.com and ghost.org crawls |
| 7 | Third-party pages need a link to be read (G-21, found by the live ghost.org crawl) | `db.identity.attributable_pages`; verified by re-extracting the stored crawl |
| 6 | Parse time limit, API rate limit (G-18) | `parse.timeout_seconds`, `api.rate_limit_per_minute`; both tested |
| 5 | Profile pages read only on identity evidence (G-05); negation and plan words refused, "start at" prices are `gte` (G-14); identity evaluation corpus (G-08) | see below |

## Checks (iteration 8, run on this machine against PostgreSQL 16 in Docker)

| Command | Result |
|---|---|
| `python -m pytest -q` | 944 passed, 9 skipped (9 were skipped at baseline too; skip reasons not re-checked) |
| `python -m ruff check .` | pass |
| `python -m ruff format --check .` | pass |
| `python -m mypy` | pass, 58 source files |
| `python -m gtm_crawler.corpus tests/corpus` | goldens reproduced; regenerated once for product extractor v3 -> v4, diff was the version line only |

Without PostgreSQL the database tests skip (742 passed, 183 skipped): that
run proves nothing about identity resolution.

## Migrations

`0015_identity_resolution`: additive (one nullable column and index on
`organization`; tables `external_identity`, `identity_decision`,
`identity_link`). Upgrade and downgrade run at the start of every test
session. Downgrade drops the three tables and with them the decisions made;
organizations, evidence and observations are untouched. Not applied to any
database other than the test database.

## Behaviour changes to know before deploying

- Dossier schema version 5 -> 6: competitors carry `identity`, and
  `organization_id` is the resolved company when linked.
- `resolve_competitor_domains` no longer writes a domain onto the named row;
  entries carry `named_organization_id` and `identity`.
- A profile found by search that is linked in neither direction no longer
  contributes observations. Dossiers that relied on such a page lose those
  values on the next extraction (they become UNKNOWN unless supported
  elsewhere).
- Statements after a negation or plan word are refused at `check_draft`.
- Third-party pages with no link to or from the company's site are no longer read (G-21). Observations already stored from such pages keep counting until each stored crawl is re-extracted: run `gtm-crawl --reextract <crawl_id>` for every crawl of an affected company.
- New settings with defaults: `parse.timeout_seconds` (30), `api.rate_limit_per_minute` (600). A client above 600 `/v1` requests a minute now gets 429.
- Extraction statistics gain `identity_unverified_pages`; `reextract` returns
  `profiles`.

## Open

| Gap | State | Needs |
|---|---|---|
| G-10, G-11 source adapters and per-source policy rules | open | which source first and which dossier fields it should feed (nothing consumes repository data today) |
| G-23 every profile a site links to is PROBABLE_MATCH | open | choose: count only header/footer/`sameAs` links, or a GitHub adapter |
| G-13 LLM extraction | blocked | provider, budget, credentials |
| G-19 live verification | verified in part (one bounded live crawl, live Exa, 2026-10-12) | more sites, a full-size crawl, a live competitor crawl; fix the three extraction errors the run found |
| G-20 tenancy | blocked | product decision |
| G-09, G-12, G-15, G-16, G-17 | deferred | reasons in the register |

## Unresolved risks

- PROBABLE_MATCH (one-way link) still admits a profile as third-party
  evidence; a hostile page that links to a company's site and has its name in
  the URL can be read. It cannot become first-party or MATCH.
- `_not_asserted` is a word list; it sees only the text before the value.
- No API for `review_link`; a wrong link is reversed from code.

## Next action

G-10 once the first source and its target fields are chosen.
