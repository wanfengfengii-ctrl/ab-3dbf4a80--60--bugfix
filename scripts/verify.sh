#!/bin/sh
# One-shot verification entrypoint for the "verify" service.
#
#   1. wait for the API health endpoint
#   2. code tests (pytest)
#   3. build check (byte-compile every module + import the app)
#   4. API smoke tests (approved and violating trajectories, 422,
#      decimal-equivalence invariance)
#
# Exits non-zero if any stage fails.
set -eu

API_BASE_URL="${API_BASE_URL:-http://api:8000}"
export API_BASE_URL
export HEALTH_PATH="${HEALTH_PATH:-/health}"

echo "==> [1/4] Waiting for API health at ${API_BASE_URL}${HEALTH_PATH}"
python scripts/wait_for_health.py

echo "==> [2/4] Running code tests"
python -m pytest tests/ -v

echo "==> [3/4] Build check (compile + import)"
python -m compileall -q app scripts tests
python -c "from app.main import app; print('import ok:', app.title)"

echo "==> [4/4] Running API smoke tests"
python scripts/smoke_test.py

echo ""
echo "VERIFY OK: all stages passed"
