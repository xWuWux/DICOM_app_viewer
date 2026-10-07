#!/usr/bin/env bash
# issue #94/#93: sentinel end-to-end leak test.
#
# A token-shaped sentinel value must appear in NO log line of any kind:
#   - nginx access.log (default format logs the raw request line, i.e.
#     query strings -- replaced with an args-free format for /api/)
#   - nginx error.log (failed-upstream lines embed the full upstream URL,
#     query included -- neutralized by `set $args ''` in the /api/ block)
#   - process argv / `ps` (the curl invocation in custom_startup.sh feeds
#     its header via --config on stdin; argv-side coverage lives in the
#     BATS suite because launching the real Kasm image here is out of
#     scope for a CI-fast job)
#
# Method (mirrors the audit's reproduction from
# docs/history/qwen_weryfikacja.md section 6): run the viewer image with
# its upstreams pointed at 127.0.0.1 (deliberately unreachable) so nginx
# produces BOTH an access line and an error line per request -- the
# worst-case logging paths -- then fire sentinel requests through every
# historical transport and grep the container's entire log output.
#
# Self-contained and self-tearing-down; safe to run anywhere Docker is.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

IMAGE="dicom-app-viewer-logleak-test:local"
CONTAINER="dicom-logleak-test"
SENTINEL_HEADER="SENTINEL-IN-HEADER-MUST-NOT-LOG"
SENTINEL_QUERY="SENTINEL-N-QUERY-MUST-NOT-LOG"
PORT="${LOGLEAK_PORT:-18980}"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "--- building the viewer image ---"
docker build -t "$IMAGE" docker/viewer

echo "--- starting viewer with unreachable upstreams (worst-case logging) ---"
cleanup
if ! docker run -d --name "$CONTAINER" \
  -p "127.0.0.1:${PORT}:8080" \
  --add-host grading-api:127.0.0.1 \
  --add-host orthanc:127.0.0.1 \
  -e ORTHANC_USER=ocr \
  -e ORTHANC_PASSWORD=testpass \
  "$IMAGE" >/dev/null; then
  echo "FAIL: could not start the test container"
  exit 1
fi

# Wait until nginx answers; if the container dies, show why instead of
# silently grepping the logs of a nonexistent container. A 502 from the
# deliberately dead upstream still proves the server itself is up.
ready=0
for _ in $(seq 1 15); do
  state=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)
  if [ "$state" != "true" ]; then
    echo "FAIL: test container exited during startup; its own output follows:"
    docker logs "$CONTAINER" 2>&1 | tail -20
    exit 1
  fi
  if curl -s -o /dev/null "http://127.0.0.1:${PORT}/api/healthz"; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != "1" ]; then
  echo "FAIL: nginx never answered on port ${PORT}"
  docker logs "$CONTAINER" 2>&1 | tail -20
  exit 1
fi

echo "--- sentinel requests: every historical token transport ---"
# 1) current transport: token as header (grading-api is unreachable, so
#    nginx still logs the request -- the point is the header value must
#    never be *reproduced* by any log).
curl -s -o /dev/null -H "X-Grading-Token: ${SENTINEL_HEADER}" \
  "http://127.0.0.1:${PORT}/api/case" || true
# 2) the rejected legacy transport: token in the query string (this is
#    the exact shape that used to be copied into access.log AND
#    error.log).
curl -s -o /dev/null "http://127.0.0.1:${PORT}/api/case?token=${SENTINEL_QUERY}" || true
# 3) error.log stress: /results query too (upstream-refused -> error
#    line with upstream URL).
curl -s -o /dev/null "http://127.0.0.1:${PORT}/api/results?token=${SENTINEL_QUERY}" || true

sleep 1
LOGS="$(docker logs "$CONTAINER" 2>&1)"

fail=0
for sentinel in "$SENTINEL_HEADER" "$SENTINEL_QUERY"; do
  if grep -qF -- "$sentinel" <<<"$LOGS"; then
    echo "FAIL: sentinel '$sentinel' found in container logs:"
    grep -F -- "$sentinel" <<<"$LOGS" | head -5
    fail=1
  else
    echo "OK: sentinel '$sentinel' absent from all container log output"
  fi
done

# Sanity guard against a vacuous pass: nginx must have logged SOMETHING
# (if it logged nothing, the greps above would be meaningless).
if ! grep -q "api/case" <<<"$LOGS"; then
  echo "FAIL: no /api/case lines in nginx logs at all -- test is vacuous"
  fail=1
else
  echo "OK: nginx demonstrably logged the requests (non-vacuous)"
fi

# Also assert the args-free format took effect: an access line exists
# WITHOUT any query string.
if grep -q '"GET /api/case HTTP' <<<"$LOGS"; then
  echo "OK: access line recorded without query args"
else
  echo "FAIL: expected an args-free access line like '\"GET /api/case HTTP/1.1\"'"
  fail=1
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
trap - EXIT

if [ "$fail" -ne 0 ]; then
  echo "test-log-leak.sh: FAILED"
  exit 1
fi
echo "test-log-leak.sh: all sentinel checks passed"
