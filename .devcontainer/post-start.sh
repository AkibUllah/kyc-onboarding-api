#!/usr/bin/env bash
# Codespaces start hook: bring up API + PostgreSQL and run the demo onboarding flow once.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d --build

echo "waiting for the API on :8000 ..."
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if [ ! -f .demo-seeded ]; then
  python scripts/demo_flow.py http://127.0.0.1:8000
  date -u +%FT%TZ > .demo-seeded
fi

cat <<'MSG'

KYC Onboarding API is up.
  API docs:   forwarded port 8000, path /docs
  Keys:       agent-dev-key (intake) · reviewer-dev-key (queue, decisions) · admin-dev-key
  Seeded:     one auto-approved applicant and one sanctions hit waiting in /v1/review/queue
MSG
