#!/usr/bin/env bash
# Generates HTTP load against the lab2 nginx instance (http://localhost:8080)
# so you can watch access logs flow nginx -> Fluent Bit -> OpenSearch, and
# trigger ISM rollover (1MB / 1 minute) on the "logs" data stream.
#
# Usage:
#   ./generate-load.sh                     # 60s, 5 req/s, concurrency 4
#   ./generate-load.sh -d 120 -r 20 -c 8    # 120s, 20 req/s, concurrency 8
#   ./generate-load.sh -n 500               # fire 500 requests as fast as possible, ignore duration/rate
set -euo pipefail

URL="http://localhost:8080"
DURATION=60
RATE=5
CONCURRENCY=4
COUNT=0

while getopts "u:d:r:c:n:h" opt; do
  case $opt in
    u) URL="$OPTARG" ;;
    d) DURATION="$OPTARG" ;;
    r) RATE="$OPTARG" ;;
    c) CONCURRENCY="$OPTARG" ;;
    n) COUNT="$OPTARG" ;;
    h)
      grep '^#' "$0" | cut -c3-
      exit 0
      ;;
    *) exit 1 ;;
  esac
done

# Mix of paths so the access log has a realistic spread of statuses.
PATHS=("/" "/" "/" "/index.html" "/missing-page" "/no-such-file.html" "/?q=test")

fire_one() {
  local path="${PATHS[$((RANDOM % ${#PATHS[@]}))]}"
  curl -s -o /dev/null "${URL}${path}"
}

if [ "$COUNT" -gt 0 ]; then
  echo "Firing $COUNT requests at $URL with concurrency $CONCURRENCY..."
  for ((i = 1; i <= COUNT; i++)); do
    fire_one &
    if (( i % CONCURRENCY == 0 )); then wait; fi
  done
  wait
  echo "Done: $COUNT requests sent."
else
  echo "Generating load against $URL for ${DURATION}s at ~${RATE} req/s (max ${CONCURRENCY} in flight)..."
  END=$(( $(date +%s) + DURATION ))
  SLEEP=$(python3 -c "print(1/${RATE})")
  SENT=0
  while [ "$(date +%s)" -lt "$END" ]; do
    fire_one &
    SENT=$((SENT + 1))
    if (( SENT % CONCURRENCY == 0 )); then wait; fi
    sleep "$SLEEP"
  done
  wait
  echo "Done: ${SENT} requests sent over ${DURATION}s."
fi
