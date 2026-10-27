# Monitoring Stack (Grafana/Prometheus/Loki/Tempo/OTel Collector/Alertmanager)

This stack is deployed via `docker compose --profile monitoring`. It is **optional** and off by default.

## Quick start
1. Copy env vars from `.env.example` (monitoring section)
2. Run `scripts/monitoring-init.sh` to render runtime configs and secrets (fails closed if required vars missing)
3. `docker compose --profile monitoring up -d --wait`
4. Access Grafana on `http://127.0.0.1:3000` (admin password from runtime file)
