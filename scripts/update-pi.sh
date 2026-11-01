#!/usr/bin/env bash
# Pulls the latest Airspace, reinstalls dependencies, rebuilds the dashboard and restarts the
# backend and kiosk browser. Leaves airspace.toml, web/.env.local and data/ alone.
#
#   ./scripts/update-pi.sh             Pull, rebuild and restart
#   ./scripts/update-pi.sh --no-pull   Rebuild and restart only, e.g. after changing web/.env.local
#
# Environment: AIRSPACE_DIR, AIRSPACE_SERVICE [airspace], AIRSPACE_PORT [8000]

set -Eeuo pipefail
trap 'echo "Error: stopped at line $LINENO of $0 ($BASH_COMMAND)" >&2' ERR

dir=${AIRSPACE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
service=${AIRSPACE_SERVICE:-airspace}
port=${AIRSPACE_PORT:-8000}
pull=1
case ${1:-} in
  --no-pull) pull=0 ;;
  "") ;;
  -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "Unknown option $1; see --help" >&2; exit 64 ;;
esac

uv=$(command -v uv || echo "$HOME/.local/bin/uv")
[ -x "$uv" ] || { echo "Error: uv not found" >&2; exit 1; }

cd "$dir"
if [ "$pull" = 1 ]; then
  echo "== git pull"
  git pull --ff-only
fi

echo "== uv sync"
# Keep FAA registry lookups if they were installed
if .venv/bin/python -c 'import duckdb' 2>/dev/null; then
  "$uv" sync --locked --extra enrich
else
  "$uv" sync --locked
fi

echo "== Building the dashboard"
[ -f web/.env.local ] || echo "Warning: web/.env.local is missing; the map needs VITE_MAPBOX_TOKEN" >&2
(cd web && npm ci && npm run build)

echo "== Restarting $service"
sudo systemctl restart "$service"
for _ in $(seq 1 45); do
  if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$port/api/health"; then
    echo "Backend is responding"
    break
  fi
  sleep 2
done
if ! curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$port/api/health"; then
  journalctl -u "$service" -n 30 --no-pager >&2 || true
  echo "Error: the backend didn't come back; see journalctl -u $service" >&2
  exit 1
fi

echo "== Reloading the kiosk browser"
"$dir/scripts/airspace-kiosk" restart --port "$port" ||
  echo "Warning: couldn't reach the desktop session; the browser will load the new build at the next boot" >&2
