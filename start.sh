#!/usr/bin/env bash
# SIGMA-HOSTING launcher: loads .env then starts the bot.
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  echo "[start.sh] Loaded .env"
else
  echo "[start.sh] No .env found - copy .env.example to .env first" >&2
fi

if [ -z "${TOKEN:-}" ]; then
  echo "[start.sh] TOKEN is not set. Aborting." >&2
  exit 2
fi

exec python3 bot.py
