#!/usr/bin/env bash
# TEST-ONLY staging rehearsal (docs/staging.md): build the image, start the
# stack in deploy/staging-test/compose.yml under its own project name, then
#   1. run the smoke test against it;
#   2. run it again while the worker is replaced mid-crawl (rolling restart:
#      SIGTERM, graceful hand-back, a new container resumes the job);
#   3. rotate the API token with the old one still accepted, then retire it.
# Reports go to $OUT (default deploy/staging-test/out). The stack and its
# volumes are removed on exit unless KEEP=1.
#
#   PYTHON=.venv/bin/python deploy/staging-test/run.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PYTHON:-python}
OUT=${OUT:-deploy/staging-test/out}
API=http://127.0.0.1:18000
SITE=http://acme-staging.test/
mkdir -p "$OUT"
GTM_API_TOKEN=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')
export GTM_API_TOKEN
compose() { docker compose -p gtmstaging -f deploy/staging-test/compose.yml "$@"; }
[ "${KEEP:-0}" = 1 ] || trap 'compose down -v --remove-orphans' EXIT

compose up -d --build --wait
smoke() { GTM_SMOKE_TOKEN=$GTM_API_TOKEN "$PY" -m gtm_crawler.smoke --api "$API" \
            --site "$SITE" --timeout 600 --poll 2; }

echo "== 1. smoke"
smoke > "$OUT/smoke.json"

echo "== 2. smoke across a worker replacement"
smoke > "$OUT/smoke-rolling.json" &
pid=$!
sleep 8  # the crawl is under way (the site has 40+ pages at 1 request/second)
compose up -d --no-deps --force-recreate worker  # SIGTERM -> hand back -> new container
wait "$pid"
compose logs worker > "$OUT/worker.log" 2>&1

echo "== 3. token rotation"
code() { curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" "$API/v1/crawls/0"; }
old=$GTM_API_TOKEN
GTM_API_TOKEN=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')
GTM_API_PREVIOUS_TOKEN=$old compose up -d --no-deps --wait api
during="new=$(code "$GTM_API_TOKEN") old=$(code "$old")"
compose up -d --no-deps --wait api  # previous token retired
after="new=$(code "$GTM_API_TOKEN") old=$(code "$old")"
echo "{\"during\": \"$during\", \"after\": \"$after\"}" | tee "$OUT/rotation.json"
[ "$during" = "new=404 old=404" ] && [ "$after" = "new=404 old=401" ]
echo "staging rehearsal passed"
