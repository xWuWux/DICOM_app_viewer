#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MON="$ROOT/deployments/monitoring"
RUNTIME="$MON/runtime"

mkdir -p "$RUNTIME"/{alertmanager,prometheus,grafana}

# Alertmanager
if [[ -z "${ASH_ALERTMANAGER_WEBHOOK_URL:-}" ]]; then
  echo "ASH_ALERTMANAGER_WEBHOOK_URL is required (http/https, no userinfo)" >&2
  exit 1
fi
WEBHOOK="$ASH_ALERTMANAGER_WEBHOOK_URL"
if [[ ! "$WEBHOOK" =~ ^https?:// ]]; then
  echo "ASH_ALERTMANAGER_WEBHOOK_URL must be http(s)" >&2
  exit 1
fi
if [[ "$WEBHOOK" =~ @ ]]; then
  echo "ASH_ALERTMANAGER_WEBHOOK_URL must not contain userinfo" >&2
  exit 1
fi
export ASH_ALERTMANAGER_WEBHOOK_URL="$WEBHOOK"
envsubst < "$MON/alertmanager/alertmanager.yml.tmpl" > "$RUNTIME/alertmanager/alertmanager.yml"
chmod 600 "$RUNTIME/alertmanager/alertmanager.yml"

# Prometheus API metrics token
METRICS_ENABLED="${ASH_METRICS_ENABLED:-false}"
if [[ "$METRICS_ENABLED" == "true" ]]; then
  TOKEN="${ASH_METRICS_TOKEN:-${ASH_METRICS_TOKEN_FILE_CONTENT:-}}"
  if [[ -z "$TOKEN" && -n "${ASH_METRICS_TOKEN_FILE:-}" && -f "${ASH_METRICS_TOKEN_FILE:-}" ]]; then
    TOKEN="$(cat "${ASH_METRICS_TOKEN_FILE:-}")"
  fi
  if [[ -z "$TOKEN" ]]; then
    echo "ASH_METRICS_TOKEN required when ASH_METRICS_ENABLED=true" >&2
    exit 1
  fi
  printf "%s" "$TOKEN" > "$RUNTIME/prometheus/api-metrics-token"
  chmod 600 "$RUNTIME/prometheus/api-metrics-token"
else
  # Create empty file to avoid mount issues if not used
  : > "$RUNTIME/prometheus/api-metrics-token"
  chmod 600 "$RUNTIME/prometheus/api-metrics-token"
fi

# Grafana admin password
if [[ -n "${GF_SECURITY_ADMIN_PASSWORD:-}" ]]; then
  printf "%s" "$GF_SECURITY_ADMIN_PASSWORD" > "$RUNTIME/grafana/admin-password"
  chmod 600 "$RUNTIME/grafana/admin-password"
fi

echo "Monitoring runtime configs rendered"
