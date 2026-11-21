#!/usr/bin/env bash
# One command: replay days with the official harness, score, run our stricter leak check and the tests.
#   ./run_eval.sh            (days 1-5)      ./run_eval.sh 1-3
set -euo pipefail
cd "$(dirname "$0")"
DAYS="${1:-1-5}"
if [ -z "${R3_PACK:-}" ]; then
  R3_PACK="$(ls -d "$PWD"/data/*/ 2>/dev/null | head -1)"; R3_PACK="${R3_PACK%/}"
fi
export R3_PACK
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
OUT="runs/$(date +%H%M%S)"
rm -rf .assist_store
"$PY" "$R3_PACK/tools/run_days.py" --cmd "$PY main.py" --days "$DAYS" --out "$OUT"
"$PY" "$R3_PACK/tools/score.py" --run "$OUT" --days "$DAYS"
"$PY" eval/validate_outputs.py --run "$OUT"
"$PY" eval/leak_check.py --run "$OUT"
"$PY" -m pytest -q tests || true
echo "outputs in $OUT"
