#!/usr/bin/env sh
set -eu

: "${PORT:=8000}"

MODEL_PRELOAD_ENABLED=false python -m flask --app backend:create_app db upgrade
exec waitress-serve --listen="0.0.0.0:${PORT}" --call backend:create_app
