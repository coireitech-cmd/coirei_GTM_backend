# Staging and rollout (Appendix B phase 17)

Spec sections 30 (deployment and operations) and 31 (secrets and
configuration). Backup and restore drills are in the disaster-recovery notes;
this document covers the staging environment, the smoke test, rolling and
canary deployment of extraction changes, rollback, and secret rotation.

## Topology

One image (`Dockerfile`) runs every role; each scales on its own.

| Service | Command | Probes | Scales |
|---|---|---|---|
| migrate | `alembic upgrade head` (one-off, before api/worker) | exit status | 1, per release |
| api | `gtm-api` (`GTM__API__HOST=0.0.0.0` in a container) | liveness `/livez`, readiness `/readyz` (database reachable, schema not behind this image's head) | stateless; any number behind a load balancer |
| worker | `gtm-worker` | liveness: its metrics port answers (`worker.metrics_port`, default 9100) | any number; jobs are claimed with `SKIP LOCKED` |
| PostgreSQL | managed where practical | | |
| raw store | S3 (`rawstore.backend = "s3"`) or a shared volume | | |

Staging is a smaller copy of production: same image, same configuration
(`deploy/config.staging.toml` equals `deploy/config.prod.toml` value for
value, enforced by a test), its own database and raw store, its own secrets.
For canary checks its database is a restored copy of production (see the
disaster-recovery notes for the restore itself).

## Configuration

`GTM_ENV=staging` loads `config.staging.toml` next to the base config and,
like `prod`, fails closed (`config._check_prod`, the same checks): it refuses
to start without `identity.user_agent_contact` and `api.token`, with the
development database URL, without `database.statement_timeout_ms`, or with
DEBUG logging, or with the Chromium sandbox off (`browser.sandbox`; the worker
container's seccomp profile and memory limit: docs/security-review.md).
In a container no base `config.toml` is needed: mount the
overlay as `/app/config.staging.toml` (or `config.prod.toml`).

`deploy/config.prod.toml` and `deploy/config.staging.toml` hold the
conservative initial budgets (pages, duration, bytes and concurrency per
crawl, browser renders, search queries, external pages, competitor crawls,
queued jobs, pool size), each commented with its default. They contain no
secrets; tests check that neither file sets a credential key and that each
budget is below the default. Raise budgets one at a time, when dashboards and
cost reports show headroom.

Secrets come only from the environment (fed by the secret manager):
`GTM__DATABASE__URL`, `GTM__API__TOKEN` (and `GTM__API__PREVIOUS_TOKEN`
during a rotation), `GTM__SEARCH__API_KEY`. All are `Secret` values: masked
in `repr`, redacted from logs, kept out of crawl configuration snapshots.

## Deploying a release

1. Build and tag the image once; the same digest goes to staging, then prod.
2. Canary gate (below) if the release changes parsing, extraction or
   assessment, or adds a migration.
3. Run `migrate` (one-off). Migrations within a release are additive (the
   gate checks this), so the running api and workers keep working on the new
   schema. Old api replicas stay ready: `/readyz` answers 200 with
   `"schema_ahead": true` for a revision their code does not ship (a newer
   release migrated). It answers 503 `migrations_pending` only when the
   schema is behind the code, so a new replica started before `migrate`
   takes no traffic.
4. Roll the api (stateless; readiness gates traffic).
5. Roll workers gradually (canary procedure below). Stopping a worker
   (SIGTERM; compose `stop_grace_period: 60s`) hands its job back at once;
   another worker resumes it from the database. Pages already fetched are
   not fetched again.
6. Smoke test (below) against the deployment.

## Smoke test

```bash
GTM_SMOKE_TOKEN=<api token> python -m gtm_crawler.smoke \
    --api https://gtm.staging.internal --site https://www.example.com/ [--timeout 900]
```

`gtm_crawler/smoke.py` checks `/livez`, `/readyz`, `/metrics`; that a request
without the token and one with a wrong token get 401; submits a crawl with an
`Idempotency-Key` and resubmits it (same job, `created: false`); polls until
the job is final (`COMPLETED` passes; `--timeout`); fetches the dossier, the
quality report and the cost; then checks the evidence invariant: every field
of the dossier (company, products, competitors) whose state is not `UNKNOWN`
(or the hand-set `NOT_APPLICABLE`) has lineage in which every item cites
observations, and every observation has an evidence quote and a stored,
unpruned raw artifact whose hash is its fetch's `raw_sha256`. A crawl that
produces no such field fails (nothing was verified). It prints a JSON report
(never the token) and exits 0 / 1 (2 for bad arguments). Use a site you are
allowed to crawl; the smoke crawl is real traffic and real cost.

## Canary procedure for extraction changes

Any build that bumps `PARSER_VERSION`, an extractor `VERSION`, or the
assessment method (`versions.current_versions`; shown per job in
`GET /v1/crawls/{id}` `versions`) goes through the gate first:

```bash
# in a checkout of the candidate build, against the staging database
GTM__DATABASE__URL=postgresql+psycopg://...@staging-db/gtm_staging \
    deploy/canary-gate.sh <git ref running in production> [--organization ID]
```

`deploy/canary-gate.sh`:

1. **Migrations are backward compatible.** Every migration added since the
   production ref is parsed; an `upgrade()` that drops or renames a table or
   column, or alters a column (`op.alter_column`, raw `DROP`/`RENAME`/
   `ALTER COLUMN`/`SET NOT NULL`), fails the gate: old workers still run during the
   rollout. Breaking changes take two releases (add and backfill, deploy; then
   tighten or drop once no old code runs); only then set
   `ALLOW_BREAKING_MIGRATION=1`.
2. **Corpus.** `python -m gtm_crawler.corpus` must match its goldens; an
   output change without a version bump fails ("bump it").
3. **Schema.** Refuses unless the database name contains `staging`, then
   `alembic upgrade head` on it.
4. **Replay.** `gtm-crawl --replay-all --fail-on-change` (a dry run: nothing
   written) re-parses every stored crawl from its raw artifacts with the new
   code. Changed observations or field states stop the gate; review
   `canary-out/replay.json` (`added`, `removed`, `changed`, field-state
   transitions, `unreadable_pages`) and rerun with `REVIEWED=1` if every
   change is intended. A crawl that fails to replay always fails the gate.
   `unreadable_pages` means the raw store was not restored with the
   database: their observations show as removed, so restore both together.

Then roll out:

- Replace one worker (the canary), watch the crawl-health dashboard and the
  alerts (`deploy/grafana`, `deploy/prometheus/alerts.yml`), compare the
  first jobs' quality reports and field-state mix with the baseline, then
  replace the rest in batches.
- Old and new workers coexist safely: jobs are claimed under leases, the
  schema is additive, and every row records the code that made it (crawl
  and job `versions`, page `parser_version`, observation
  `extractor@version`, assessment `method_version`). A job that straddles
  the rollout (handed back by an old worker, resumed by a new one) may have
  pages parsed by both parser versions; its rows say which, and
  `gtm-crawl --replay ID --write` re-extracts it uniformly if needed.
- Do not change `[urls]`, `[parse]` or `[frontier]` *settings* in the same
  rollout as running jobs: a resumed crawl refuses changed settings (its
  stored locators depend on them). Drain or pause jobs first.
- After the rollout, optionally `gtm-crawl --replay-all --write` to bring
  existing companies to the new extractors (new extraction runs; old ones
  stay queryable).

## Rollback

- **Code:** redeploy the previous image tag (api, then workers, the same
  rolling way). Because the release's migrations are additive, the previous
  code runs on the new schema (its `/readyz` reports `schema_ahead`); do not
  downgrade the schema to roll back code.
- **Data written by the bad build:** extraction runs and assessments are
  append-only and versioned. Replay the affected crawls with the good build
  (`gtm-crawl --replay ID --write`): the new run supersedes the bad one and
  the dossier and lineage follow it.
- **Migration downgrade policy:** every migration has a `downgrade()`, but
  downgrading production drops data the newer schema holds (columns,
  tables, history). Allowed only to recover from a migration that failed
  half-way or is itself wrong, after a backup, with the API and workers
  stopped: `alembic downgrade <revision>`. Otherwise roll forward with a
  fix. Rehearse any downgrade on the staging copy first.

## Secret rotation

Settings are read once at process start, so every rotation is a rolling
restart; the worker hands its job back on SIGTERM, so no work is lost.

- **API token** (`GTM__API__TOKEN`): set the new token as `GTM__API__TOKEN`
  and the old one as `GTM__API__PREVIOUS_TOKEN`; restart the api replicas.
  Both are accepted (each compared in constant time). Move every client to
  the new token, then unset `GTM__API__PREVIOUS_TOKEN` and restart again.
  `previous_token` without `token` is refused (it would leave the API open).
- **Exa key** (`GTM__SEARCH__API_KEY`): create the new key with the
  provider, set it, restart workers in a rolling way, then revoke the old
  key once no worker uses it (the provider accepts both meanwhile). Only
  workers use it.
- **Database password** (in `GTM__DATABASE__URL`): add the new password
  (or a second role) in PostgreSQL, roll api and workers onto it, then drop
  the old one.
- **S3 credentials** come from the AWS chain (role or environment);
  rotate them there, then restart.

## What was tested

Recorded on 2026-10-01 (Windows 11, Docker 29.5.3, Compose 5.1.4).

In pytest (`tests/test_staging_phase17.py`): the staging profile fails
closed exactly like prod (same problems, built from files, env or code);
both example configs load under their profile, contain no secret keys, have
budgets below the defaults, allow only public networks, and are identical;
`previous_token` rotation (new, old, wrong, missing, non-ASCII header); the
smoke test end to end in process (the real API through FastAPI's TestClient
and a real worker in a thread, on a simulated site) passes every check, and
fails as expected for a wrong token, an unreachable API, and each broken
lineage link.

`deploy/canary-gate.sh` against the test database: additive-only migrations
pass; a range including migrations 0004 and 0006 is refused
(`op.alter_column`); a database whose name lacks `staging` is refused; replay
with the raw store missing reports `unreadable_pages` and a change, and the
gate stops.

The compose rehearsal (`deploy/staging-test/run.sh`): the image built from
this tree, `GTM_ENV=staging` with `deploy/config.staging.toml`, its own
PostgreSQL (project `gtmstaging`, no host port but the API's 127.0.0.1:18000),
a synthetic 43-page site, the smoke test run from the host. Results:

| Run | Result |
|---|---|
| Smoke | every check passed in 46.7 s: probes 200, unauthenticated and wrong-token requests 401, submit 201, resubmit 200 with the same job, crawl `COMPLETED` (43 fetches, 43 pages parsed), dossier, quality and cost 200, evidence invariant over 5 stated fields / 7 observations with no problem |
| Smoke across a worker replacement | the worker was force-recreated 8 s into the crawl (SIGTERM, hand-back); the new container logged `crawl.resumed` and fetched the remaining 35 pages; every check passed in 48.5 s (5 fields / 14 observations) |
| Token rotation | with `previous_token` set: new 404, old 404 (both authorized); after retiring it: new 404, old 401 |

The `api` and `worker` healthchecks in the rehearsal compose file (readiness
`/readyz`, worker metrics port) gated `docker compose up --wait`. The stack
and its volumes were removed afterwards.

## Not tested

- A real staging environment (managed PostgreSQL, S3, a load balancer,
  several hosts) and real public sites: the rehearsal stack is one machine.
- Search and competitor crawls in the rehearsal (no Exa key; the
  synthetic site has no competitors).
- The canary gate against a restored production copy (none exists yet);
  it ran against the test database.
- A rollout with a real extractor version bump and an actual old/new worker
  mix on one job; coexistence rests on the additive-migration check and the
  version columns described above.
