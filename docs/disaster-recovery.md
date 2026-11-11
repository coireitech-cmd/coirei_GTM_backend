# Disaster recovery (Phase 17)

Spec section 30 ("Backups/PITR where supported", "Regular restore drills",
"Document RPO/RTO") and section 35 ("Backups can be restored", "Crawl resumes
after restart", "Extraction is replayable", "Every factual field has evidence
or is UNKNOWN").

## What has to survive

| Store | Holds | Losing it means |
|---|---|---|
| PostgreSQL | jobs, crawl state and frontier, fetches, pages, evidence, observations, assessments, artifact records | everything; it is the source of truth |
| Raw store (filesystem or S3) | response bodies, gzip, named by SHA-256 | evidence quotes can no longer be checked against the original, and replay of those pages is impossible |

Raw documents are written (fsynced, or acknowledged by S3) *before* any row
cites them (`rawstore.py`), so a consistent database snapshot never cites a
document that was not already stored. The raw store is content-addressed and
append-only apart from retention pruning, so copying it is incremental and
idempotent.

## RPO / RTO targets and how they are met

| Deployment | RPO (data loss) | RTO (time to serve again) | How |
|---|---|---|---|
| Managed PostgreSQL with PITR (RDS, Cloud SQL, Azure Flexible Server) + S3 raw store with versioning | minutes (WAL archiving interval, typically <= 5 min) | < 1 h | provider point-in-time restore to a new instance; bucket versioning keeps deleted or overwritten objects; point the config at the new instance; `gtm-crawl --verify`; start workers |
| Self-managed PostgreSQL, nightly `python -m gtm_crawler.backup backup` | 24 h | < 1 h while the filesystem raw store holds fewer than about 200 000 documents (measured rates below: raw copy plus verify dominate; pg_dump/pg_restore are under a second at these sizes) | restore the latest backup (runbook below) |
| Filesystem raw store | same as the database backup | same | copied by the same backup, after the dump |

Targets: **RPO 24 h** for the self-managed setup (nightly dump), **RPO <= 15
min** where managed PITR is available (recommended for production); **RTO 1
h** for either. What is lost within the RPO is recoverable work, not facts
nobody can re-derive: re-submitting the affected companies re-crawls them, and
evidence is always re-read from the source.

S3 raw store: do not copy it; enable **bucket versioning** (a deleted or
replaced object can be restored) and, for site loss, **cross-region
replication**. Add a lifecycle rule that expires *noncurrent* versions after
the database backup retention (e.g. 35 days) so a database restored from an
old backup still finds every body it cites. The backup tool records
`"raw": {"backend": "s3", "copied": false}` in that case.

## Measured (restore drill)

`python -m gtm_crawler.drill --pages N` (below), on the load-test machine
(docs/load-results.md: Windows 11, 16 logical CPUs, PostgreSQL 16.15 in
Docker Desktop; `pg_dump`/`pg_restore` run inside the container through
`docker exec`), two simulated sites of N pages each, local-disk raw store.

All three runs passed every check. Seconds are wall time; sizes in bytes.

| Pages per site | 200 | 1000 | 3000 |
|---|---:|---:|---:|
| Database size (`pg_database_size`) | 11.8 MB | 15.3 MB | 24.3 MB |
| Dump size (`-Fc`) | 0.22 MB | 0.70 MB | 1.91 MB |
| Raw objects / bytes (gzip) | 272 / 0.12 MB | 1 337 / 0.62 MB | 4 007 / 1.87 MB |
| **Backup total** | **2.4 s** | **8.9 s** | **26.5 s** |
| &nbsp; pg_dump | 0.30 | 0.41 | 0.59 |
| &nbsp; raw-store copy | 1.83 | 8.26 | 25.6 |
| **Restore total** | **2.6 s** | **13.4 s** | **59.6 s** |
| &nbsp; pg_restore | 0.62 | 0.64 | 0.82 |
| &nbsp; raw-store copy | 1.52 | 8.62 | 36.5 |
| &nbsp; alembic upgrade head | 0.05 | 0.08 | 0.06 |
| &nbsp; verify (just-copied files) | 0.41 | 3.95 | 22.2 |
| Resume: interrupted crawl to completion | 6.1 s | 30.7 s | 99.5 s |
| Leased URLs in the backup / lost | 6 / 0 | 5 / 0 | 7 / 0 |
| verify afterwards (documents, s) | 397, 0.12 | 2 001, 0.58 | 6 000, 2.42 |

Rates to extrapolate from (this machine, small databases, so treat as a lower
bound for large ones): `pg_dump -Fc` and `pg_restore` are dominated by fixed
cost at these sizes; the filesystem raw-store copy runs at about 110-160
objects/s (one small file per document, Windows NTFS; Defender scanning new files is the likely cost); `verify` reads and
hashes about 2 500 documents/s from a warm cache, but only about 180/s over files just written (the restore's verify). A production-sized raw store (millions of
documents) therefore takes hours to copy file by file: that is why the S3
backend with versioning, not copying, is the production recommendation, and
why the filesystem copy is incremental (only new objects are copied when the
same backup directory's `raw/` is reused as a seed).

## Tools

* `python -m gtm_crawler.backup backup --out DIR`: `pg_dump --format=custom`
  of the configured database to `DIR/db.dump`, **then** the filesystem raw
  store to `DIR/raw/`, then `DIR/manifest.json` (written last; a directory
  without it is an incomplete backup). The manifest records the snapshot time
  (the recovery point), database name/host/size, the alembic revision in the
  database, the code's migration head, crawler and component versions
  (`versions.py`), the dump's size and SHA-256, the `pg_dump` version, and the
  raw object count and bytes.
* `python -m gtm_crawler.backup restore DIR`: refuses an incomplete backup, a
  dump whose SHA-256 does not match the manifest, or a non-empty target
  database; then `pg_restore --no-owner --no-acl --single-transaction` (a
  failed restore leaves the database empty), copies `DIR/raw/` into the
  configured raw store, runs `alembic upgrade head` (so a backup from older
  code is brought to the current schema), and runs `verify`. Exit status 1 if
  verification finds problems.
* `gtm-crawl --verify`: read-only integrity check (one REPEATABLE READ, READ
  ONLY transaction, rows streamed, each document read and hashed once). Every
  artifact the database records as present must exist in the raw store with a
  matching SHA-256; an artifact cited by evidence or a robots.txt snapshot must
  never have been pruned; every non-UNKNOWN field assessment must reference
  existing observations whose evidence is on a page whose retrieval has a raw
  artifact; and no valued state may lack observations. Prints counts, problem
  counts and up to 20 samples per problem kind as JSON; exit 1 on any problem.
  Problem kinds: `missing_cited`, `missing`, `corrupt`,
  `cited_artifact_pruned`, `missing_observation`, `no_raw_artifact`,
  `value_without_observations`.
* `python -m gtm_crawler.drill`: the restore drill (below).

`pg_dump`/`pg_restore` are external binaries; they must be the server's major
version or newer. `GTM_PG_COMMAND_PREFIX` (or `--pg-prefix`) is put in front
of them, e.g. with the compose stack:

```
GTM_PG_COMMAND_PREFIX="docker exec -i -e PGPASSWORD scraper10-postgres-1"
```

The database URL is then resolved inside that container. The password is
passed in `PGPASSWORD` (forwarded by `-e PGPASSWORD`), never on a command
line. Without a prefix the binaries on `PATH` are used.

**Why the database before the raw store.** Bodies are stored before the rows
that cite them. Dumping first fixes the set of cited digests at the snapshot;
every one of them is already in the store when the copy starts, and documents
stored later are copied too (harmless). Copying the store first would let a
crawl commit rows between the copy and the dump that cite bodies the copy
missed. Do not run `gtm-crawl --prune` during a backup: it could delete an
uncited body after the dump recorded it as present (`verify` would then
report it `missing`; nothing cited is affected, since cited bodies are never
pruned).

Backups contain everything the crawler stored (raw pages, evidence, job
history). The database holds no credentials, but treat backup directories as
confidential: restrict access, encrypt at rest, keep them off the database
host.

## Runbook

**Back up** (nightly, self-managed PostgreSQL):

```
python -m gtm_crawler.backup backup --out /backups/gtm/$(date -u +%Y%m%dT%H%MZ)
```

Keep 35 days. Workers and the API may keep running (pg_dump reads one
consistent snapshot); do not prune during it. Alert if a night's directory
has no `manifest.json`.

**Restore**:

1. Stop the API and all workers (nothing may write to the old or new database).
2. Create an empty database (`CREATE DATABASE gtm_crawler`) and an empty raw
   store directory; point the configuration (`GTM__DATABASE__URL`,
   `rawstore.root`) at them.
3. `python -m gtm_crawler.backup restore /backups/gtm/<latest>`: checks the
   dump hash, restores, copies raw documents, migrates to head, verifies.
   With managed PITR / S3 versioning instead: restore the instance to the
   chosen time with the provider's tooling, run `alembic upgrade head`, then
   `gtm-crawl --verify`.
4. If verification reports problems, see below; do not start workers on a
   database that fails verification without deciding what to do about them.
5. Start the API, then workers. Jobs that were running at the snapshot have
   leases that have expired by now: workers reclaim them (`reclaimed` job
   event) and each crawl resumes from its durable frontier; leased URLs are
   picked up again, none are lost (drill check "no leased URL lost").
   Anything submitted after the snapshot is gone: resubmit those companies.
6. Optionally `gtm-crawl --replay-all --fail-on-change`: replays every
   finished crawl from the restored raw documents and must report no changes.

**Raw documents missing or corrupt** (`missing_cited`, `missing`, `corrupt`):

* Look for the bodies elsewhere first: an older backup's `raw/`, S3 noncurrent
  versions. Bodies are content-addressed, so any copy with the right name and
  hash is the right body; `verify` again afterwards.
* If a body is gone for good, its evidence rows stay as they are (they record
  what was quoted, where and when), but that page can no longer be re-checked
  or replayed: replay lists it in `unreadable_pages`, and its observations
  show as removed in the comparison. **Never fabricate or re-fetch a
  substitute under the old hash**: a re-fetch is a new retrieval, recorded as
  a new fetch with its own hash and date. To get the facts re-verified,
  resubmit the company so it is crawled again.
* `corrupt` bodies: delete the damaged file so the next crawl that fetches the
  same bytes stores it again, and treat as missing.

**Broken lineage** (`missing_observation`, `no_raw_artifact`,
`value_without_observations`): a restore from an inconsistent source (e.g. a
partial manual restore). Re-assess the organization (`gtm-crawl --reextract
<crawl id>`) so current assessments are rebuilt from existing observations;
anything without evidence becomes UNKNOWN.

## Restore drill

```
GTM_PG_COMMAND_PREFIX="docker exec -i -e PGPASSWORD scraper10-postgres-1" \
  python -m gtm_crawler.drill --pages 1000 --out drill.json
```

Scratch database `GTM_DRILL_DATABASE_URL` / `--database-url` (default
`gtm_crawler_drill_scratch`; names without "drill" are refused; dropped and
recreated). Against a simulated web (the load-test sites, `loadtest.py`) it:

1. crawls company A to completion, records its dossier, runs `verify`;
2. starts crawl B in a worker process and kills it a third of the way in
   (leases stay held, as after a crash);
3. backs up;
4. drops the database and deletes the raw store;
5. restores (pg_restore, raw copy, `alembic upgrade head`, `verify`);
6. checks A's dossier is identical to before (only `generated_at`, the time
   the dossier was built, is ignored), a dry-run replay of A's crawl reports no
   added/removed/changed observations and no unreadable pages, the backup
   caught B mid-crawl with URLs leased, and B resumes and completes with none
   of those URLs lost;
7. runs `verify` again after the resumed crawl.

It prints PASS/FAIL per check and the timings and sizes; exit 1 on failure.
`tests/test_dr_phase17.py` runs it at 30 pages per site (skipped when
`pg_dump` cannot be run through the prefix), then damages the restored data
(deletes a cited body, rewrites another, adds an assessment citing a missing
observation) and checks `verify` / `gtm-crawl --verify` report exactly those
problems and exit 1, and that `restore` refuses a non-empty database, a
tampered dump and a directory without a manifest.

**Schedule:** run the drill in CI on every change to `backup.py`, the
migrations or `rawstore.py` (the pytest does), and against a copy of the
production backup **monthly** (restore the latest real backup into a scratch
"drill" database and raw store, `verify`, `--replay-all --fail-on-change` on a
sample) and after every PostgreSQL major-version upgrade. Record the measured
restore time; if it exceeds the RTO, move to PITR / S3 versioning.

## What was not tested

* Managed-PostgreSQL PITR and S3 bucket versioning/replication restores: they
  are provider features, documented above, not exercised here (no cloud
  account in the test environment). `verify` itself works against the S3
  backend (it only uses `RawStoreBackend.get`), but was run here only on the
  filesystem backend.
* A backup taken while workers are actively writing: the drill backs up after
  the crawl worker was killed. Consistency of a live dump rests on pg_dump's
  single snapshot and the write order above, not on a test.
* Production-sized data: the drill's databases are tens of MB. pg_dump/
  pg_restore time grows with database size; measure on a real backup before
  relying on the RTO.
* Restoring with `pg_dump`/`pg_restore` binaries outside the container (only
  the `docker exec` prefix was exercised), or across PostgreSQL major versions.
* Loss of individual tables or partial corruption inside PostgreSQL (the plan
  for those is a full restore).
