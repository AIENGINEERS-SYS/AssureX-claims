#!/usr/bin/env sh
set -eu

: "${PORT:=8000}"

# Migrations do not need ML artifacts in memory.
MODEL_PRELOAD_ENABLED=false MODEL_PRELOAD_STRICT=false \
  python -m flask --app backend:create_app db upgrade

# Report exports are queued in the database. Railway's API service also owns the
# local report filesystem, so keep the worker in this service by default. The
# worker explicitly disables ML preloading to avoid a second TensorFlow/XGBoost
# copy competing with the web process for memory.
case "${REPORT_WORKER_ENABLED:-true}" in
  1|true|TRUE|yes|YES)
    (
      while :; do
        if MODEL_PRELOAD_ENABLED=false MODEL_PRELOAD_STRICT=false \
            python -m flask --app backend:create_app report-worker; then
          status=0
        else
          status=$?
        fi
        echo "AssureX report worker exited with status ${status}; restarting in 2 seconds." >&2
        sleep 2
      done
    ) &
    ;;
  0|false|FALSE|no|NO)
    echo "AssureX report worker disabled by REPORT_WORKER_ENABLED." >&2
    ;;
  *)
    echo "REPORT_WORKER_ENABLED must be true/false, yes/no, or 1/0." >&2
    exit 2
    ;;
esac

exec waitress-serve --listen="0.0.0.0:${PORT}" --call backend:create_app
