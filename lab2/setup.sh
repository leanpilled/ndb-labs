#!/usr/bin/env bash
# Part 3: ISM policy + index template + data stream for nginx logs.
#
# Must run in this order:
#   1. job_interval=1 — MUST be set before the data stream is created. The
#      per-index ISM job schedule is captured once, at the moment the index
#      starts being managed, and is NOT updated retroactively if you change
#      this cluster setting later (see lab notes).
#   2. ISM policy "logs_policy" — rollover at 1mb OR 1 minute (whichever
#      first), delete 10 minutes after creation. Attaches automatically to
#      "logs" / ".ds-logs-*" via ism_template.
#   3. Index template "logs-template" — nginx log field mappings, and
#      index.routing.allocation.require.temp: hot so backing indices are only
#      ever allocated to hot nodes (not warm) — logs live 10 minutes, there's
#      no point migrating them to warm storage.
#   4. Data stream "logs" — safe to skip if it already exists.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
set -a; source .env; set +a

AUTH=(-u "admin:${ADMIN_PASSWORD}")

echo "=== 1. ISM job_interval=1 (must precede data stream creation) ==="
curl -sk "${AUTH[@]}" -X PUT "https://localhost:9200/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d '{"persistent":{"plugins.index_state_management.job_interval":1}}' -w "\nHTTP %{http_code}\n"

echo "=== 2. ISM policy logs_policy ==="
POLICY_URL="https://localhost:9200/_plugins/_ism/policies/logs_policy"
EXISTING=$(curl -sk "${AUTH[@]}" "$POLICY_URL")
if echo "$EXISTING" | grep -q '"_seq_no"'; then
  SEQ_NO=$(echo "$EXISTING" | python3 -c "import json,sys;print(json.load(sys.stdin)['_seq_no'])")
  PRIMARY_TERM=$(echo "$EXISTING" | python3 -c "import json,sys;print(json.load(sys.stdin)['_primary_term'])")
  POLICY_URL="${POLICY_URL}?if_seq_no=${SEQ_NO}&if_primary_term=${PRIMARY_TERM}"
fi
curl -sk "${AUTH[@]}" -X PUT "$POLICY_URL" \
  -H 'Content-Type: application/json' \
  -d '{
  "policy": {
    "description": "Rollover logs backing index at 1MB or 1 minute, delete whole stream data after 10 minutes",
    "default_state": "hot",
    "states": [
      {"name": "hot", "actions": [{"rollover": {"min_size": "1mb", "min_index_age": "1m"}}],
       "transitions": [{"state_name": "delete", "conditions": {"min_index_age": "10m"}}]},
      {"name": "delete", "actions": [{"delete": {}}], "transitions": []}
    ],
    "ism_template": [{"index_patterns": ["logs", ".ds-logs-*"], "priority": 100}]
  }
}' -w "\nHTTP %{http_code}\n"

echo "=== 3. index template logs-template ==="
curl -sk "${AUTH[@]}" -X PUT "https://localhost:9200/_index_template/logs-template" \
  -H 'Content-Type: application/json' \
  -d '{
  "index_patterns": ["logs"], "data_stream": {}, "priority": 100,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "index.routing.allocation.require.temp": "hot"
    },
    "mappings": {"properties": {
      "@timestamp": {"type": "date"}, "remote_addr": {"type": "ip"},
      "request_method": {"type": "keyword"}, "request_uri": {"type": "keyword"},
      "status": {"type": "integer"}, "body_bytes_sent": {"type": "long"},
      "request_time": {"type": "float"}, "http_referer": {"type": "keyword"},
      "http_user_agent": {"type": "text"}
    }}
  }
}' -w "\nHTTP %{http_code}\n"

echo "=== 4. data stream logs ==="
if curl -sk "${AUTH[@]}" -o /dev/null -w "" --fail "https://localhost:9200/_data_stream/logs" 2>/dev/null; then
  echo "data stream 'logs' already exists, skipping"
else
  curl -sk "${AUTH[@]}" -X PUT "https://localhost:9200/_data_stream/logs" -w "\nHTTP %{http_code}\n"
fi

echo "Done."
