#!/usr/bin/env bash
# TEST-ONLY browser containment check (docs/security-review.md, "Browser
# containment, proven in the container"). Runs image $IMAGE (default
# gtm-crawler; build it with `docker build -t gtm-crawler .`) as the
# production worker runs: non-root, deploy/chrome-seccomp.json, no added
# capabilities, not privileged. Reports go to $OUT (default out/).
#   1. render: real renders with browser.sandbox on; every renderer process
#      has Chromium's seccomp-bpf filter, NoNewPrivs and its own user, pid and
#      net namespaces (probe.py checks); container peak memory.
#   2. DNS: the fixture is the only DNS server; the only names looked up are
#      the ones the crawler's guard resolved (page and script hosts), none of
#      the *-leak.test names the page tries to reach.
#   3. control: plain Chromium on the same page does look up *-leak.test
#      names, so the DNS log would catch a leak.
#   4. memory limit: at 768 MiB a DOM bomb's renderer is OOM-killed, the
#      worker process survives and the next render works.
#
#   deploy/sandbox-test/run.sh            (IMAGE=... to test another image)
set -euo pipefail
cd "$(dirname "$0")"
export IMAGE=${IMAGE:-gtm-crawler} MSYS_NO_PATHCONV=1  # (Git Bash: keep /t/... paths)
OUT=${OUT:-out}
mkdir -p "$OUT"
compose() { docker compose -p gtmharden -f compose.yml "$@"; }
[ "${KEEP:-0}" = 1 ] || trap 'compose down -v --remove-orphans' EXIT
probe() { PROBE_MEMORY=${2:-2560m} compose run --rm -T probe python /t/probe.py "$1"; }
dns() { compose logs --no-log-prefix fixture | awk '$1 == "dns" {print $2}' | sort -u; }

compose up -d fixture

echo "== 1. sandboxed renders"
probe render > "$OUT/render.json"

echo "== 2. DNS"
names=$(dns | tr '\n' ' ')
echo "names looked up: $names" | tee "$OUT/dns.txt"
[ "$names" = "acme.test cdn-ok.test " ] || { echo "FAIL unexpected DNS names"; exit 1; }

echo "== 3. control"
probe control > "$OUT/control.json"
leaks=$(dns | grep -- '-leak\.test$' | tr '\n' ' ' || true)
echo "plain Chromium looked up: $leaks" | tee -a "$OUT/dns.txt"
[ -n "$leaks" ] || { echo "FAIL the control looked up nothing: a leak would go unseen"; exit 1; }

echo "== 4. memory limit"
probe oom 768m > "$OUT/oom.json"
echo "sandbox test passed"
