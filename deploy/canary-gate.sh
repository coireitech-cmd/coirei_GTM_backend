#!/usr/bin/env bash
# Canary gate for a build that changes extraction (docs/staging.md, spec 30:
# "Rolling/canary deployment for extraction changes"). Run from "gtm-crawler"
# in a checkout of the candidate build, BEFORE any worker runs it:
#
#   GTM__DATABASE__URL=postgresql+psycopg://...staging... \
#       deploy/canary-gate.sh <ref-running-in-production> [--organization ID]
#
# 1. Migrations added since <ref> must be backward compatible: old workers
#    keep running against the new schema during the rollout, so upgrade()
#    may not drop, rename or tighten (alter_column) anything. Override with
#    ALLOW_BREAKING_MIGRATION=1 only for the two-release procedure.
# 2. The artifact corpus must match its goldens (output changes need a version bump).
# 3. The schema of the staging database (a restored copy of production;
#    the name must contain "staging") is upgraded to this build's head.
# 4. Replay (dry run: nothing is written) of every stored crawl with this
#    build's parser and extractors; any changed observation or field state
#    stops the gate. Review $OUT/replay.json, then rerun with REVIEWED=1.
#
# Exit 0: gate passed, roll workers out gradually. Non-zero: do not deploy.
set -euo pipefail
BASE=${1:?usage: canary-gate.sh <production-ref> [--organization ID]}
shift
PY=${PYTHON:-python}
OUT=${OUT:-canary-out}
mkdir -p "$OUT"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

echo "== 1. migrations since $BASE"
git diff --relative --name-only --diff-filter=AM "$BASE" -- gtm_crawler/db/migrations/versions/ \
    > "$OUT/migrations.txt"
"$PY" - "$OUT/migrations.txt" <<'EOF'
import ast, os, re, sys
from pathlib import Path

BREAKING = {"drop_column", "drop_table", "rename_table", "alter_column"}
SQL = re.compile(r"\b(DROP\s+(TABLE|COLUMN)|RENAME|ALTER\s+COLUMN|SET\s+NOT\s+NULL)\b", re.I)
found = []
for name in Path(sys.argv[1]).read_text().split():
    tree = ast.parse(Path(name).read_text(encoding="utf-8"))
    up = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    for node in ast.walk(up):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            op = node.func.attr
            text = ast.unparse(node)
            if op in BREAKING or (op == "execute" and SQL.search(text)):
                found.append(f"{name}:{node.lineno}: {text[:120]}")
print("\n".join(found) or "additive only")
if found and os.environ.get("ALLOW_BREAKING_MIGRATION") != "1":
    sys.exit("breaking migration: old workers would fail during the rollout (docs/staging.md)")
EOF

echo "== 2. corpus"
"$PY" -m gtm_crawler.corpus

echo "== 3. staging database"
"$PY" - <<'EOF'
import sys
from sqlalchemy.engine import make_url
from gtm_crawler.config import load_settings

name = make_url(load_settings().database.url).database or ""
if "staging" not in name:
    sys.exit(f"refusing: database {name!r} is not a staging database")
print(f"database {name}")
EOF
alembic upgrade head

echo "== 4. replay (dry run)"
"$PY" -m gtm_crawler.cli --replay-all --fail-on-change "$@" > "$OUT/replay.json" || true
"$PY" - "$OUT/replay.json" <<'EOF'
import json, os, sys

r = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"changed": r["changed"], "totals": r.get("totals")}, default=str)[:2000])
if failed := r.get("totals", {}).get("failed", 0):
    sys.exit(f"{failed} crawl(s) failed to replay; see replay.json (not overridable)")
if r["changed"] and os.environ.get("REVIEWED") != "1":
    sys.exit("replay changed observations or field states: review replay.json; "
             "if every change is intended, rerun with REVIEWED=1")
EOF
echo "canary gate passed"
