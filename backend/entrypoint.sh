#!/bin/sh
set -e

# Railway (and similar Docker hosts) have no native way to mount a secret
# file, so the service-account key travels as a raw-JSON env var instead and
# gets materialized to disk here before the app starts.
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS_JSON" ]; then
  echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /app/service-account.json
  export GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
