# Entity resolution

Which real company a record is. Code: `gtm_crawler/identity.py` (comparable
names, profile URL -> identifier), `gtm_crawler/db/identity.py` (decisions and
links), migration `0015`. Algorithm version `ir-v1`
(`db.identity.ALGORITHM_VERSION`), stored on every decision.

## Rule

Two records are the same company only on evidence we hold: links between
pages we fetched. A name, a URL slug, a description or a search result never
decides it. Without evidence the records stay apart and the outcome is
UNKNOWN.

Nothing is merged. A record is linked to a company; facts stay on the rows
they were recorded on and links are followed when they are read. Undoing a
link is a status change, so no dependent evidence or relationship is rewritten.

## Records

| Table | What it is |
|---|---|
| `organization` | A company (has `registrable_domain`), or a name another company used (`named_by_org_id` set, no domain). The same name used by two companies is two rows. |
| `external_identity` | A profile on another platform: `(source_type, external_id)`, unique; `profile_url` as first seen. |
| `identity_decision` | One resolution attempt: outcome, candidates, signals and contradictions (each naming `link.id` / `page.id`), rationale, algorithm version, actor, automatic or manual, crawl, the decision it supersedes. Append-only by trigger. |
| `identity_link` | The current answer for one pair (named organization or profile -> company): `match`, `probable_match`, `conflicting`, `rejected`, with the decision that set it. |

Constraints: a named organization has at most one link that is not rejected;
a profile at most one `match`; a row with a domain cannot be a member (no
chains, no cycles); one link per pair (retries are idempotent).

## Outcomes

Named organization (`resolve_member`; candidates come from
`pipeline.resolve_competitor_domains`: links on the pages that named it whose
anchor text is the name or whose domain label is its slug):

| Evidence | Outcome |
|---|---|
| one candidate domain | MATCH |
| none | UNKNOWN |
| several | CONFLICTING |
| one, rejected by a reviewer | UNKNOWN |
| one, differing from the current link | CONFLICTING; the link is no longer followed |

Profile (`resolve_profiles`, run before every extraction):

| Evidence | Outcome |
|---|---|
| site links the profile and the fetched profile links back | MATCH |
| one direction only | PROBABLE_MATCH |
| fetched, no link either way | UNKNOWN (decision, no link) |
| another company claims it too | CONFLICTING, unless exactly one claim is reciprocal: that one is MATCH |
| several reciprocal profiles of one platform for one company | CONFLICTING, all of them, until a reviewer accepts one (a person's account links to the employer as the company's own does) |

## What the outcome changes

- Dossier: a competitor shows the company's own assessed fields only through
  a MATCH link (`dossier._build_dossier`, `identity` key on the competitor).
- Extraction: a page on any other site is read for facts about a company
  only if it is its profile with MATCH or PROBABLE_MATCH, or it links to the
  company's registrable domain, or the company's own site links to that
  exact URL (`db.identity.attributable_pages`). Otherwise the page is kept
  and not read (`identity_unverified_pages` in extraction statistics). The
  company's name on the page is not evidence: other companies share it.
- Competitor crawl: only a MATCH gives a seed.

## Review

`review_link(conn, link_id, accept=..., actor=..., rationale=...)` confirms or
rejects a link. The manual decision supersedes the automatic one; the
resolver never changes a reviewed link, so reprocessing does not restore a
rejected one. There is no API endpoint for it yet (called from code or a
shell).

## Normalization

`normalize_name`: NFKC, casefold, punctuation removed, trailing legal forms
dropped ("Acme Holdings Ltd." -> "acme holdings"). Accents and words such as
Holdings, Group, Labs are kept. Used to compare, never to decide. Domains use
`urls.registrable_domain`. `external_id(url)` maps a profile URL to
`(source_type, id)`; persons, posts, share links and repositories are not
company profiles.

## Evaluation

`tests/test_identity_eval.py`: labelled cases scored together. False merges
must be 0. Unit and integration cases: `tests/test_identity.py`,
`tests/test_identity_resolution.py`, `tests/test_pipeline.py`,
`tests/test_external.py`.

## Limits

- Rows named before migration 0015 keep `named_by_org_id` NULL and are never
  matched by name again; they stay as history.
- A one-way link is weak: any page can link to a company's site.
  PROBABLE_MATCH pages are read as third-party evidence, never first-party.
- A mention with no link on its page stays UNKNOWN; no review queue of
  same-name candidates exists (gap G-09).
- One link is enough for a third-party page, so a page about two same-name
  companies that links to this one is still read.
- A person's profile that is the only reciprocal one on its platform is
  still MATCHed as the company's: URLs do not tell a user from an
  organization. Every profile a site links to is PROBABLE_MATCH, including
  its authors' personal accounts (gap G-23).
- Only organizations are resolved. Persons and products are not.
- Metric: `identity_decisions_total{outcome,kind}`.
