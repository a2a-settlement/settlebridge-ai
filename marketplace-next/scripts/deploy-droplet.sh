#!/usr/bin/env bash
# Production deploy for market.settlebridge.ai (Next.js standalone + systemd).
# Run on the droplet as root:  sudo bash /home/clawdbot/settlebridge-ai/marketplace-next/scripts/deploy-droplet.sh
set -euo pipefail

APP="/home/clawdbot/settlebridge-ai/marketplace-next"
SERVICE="settlebridge-marketplace.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (e.g. sudo bash $0)" >&2
  exit 1
fi

if [[ ! -d "$APP" ]]; then
  echo "Missing $APP" >&2
  exit 1
fi

echo "[deploy] npm run build (as clawdbot)..."
runuser -u clawdbot -- bash -lc "cd '$APP' && npm run build"

echo "[deploy] sync .next/static + public into standalone..."
runuser -u clawdbot -- bash -lc "
  cd '$APP'
  rm -rf .next/standalone/.next/static .next/standalone/public
  cp -r .next/static .next/standalone/.next/static
  cp -r public .next/standalone/public
"

echo "[deploy] ensure clawdbot owns .next..."
chown -R clawdbot:clawdbot "$APP/.next"

echo "[deploy] restart $SERVICE..."
systemctl restart "$SERVICE"
sleep 1
systemctl is-active --quiet "$SERVICE" || {
  echo "[deploy] service failed; journal:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2
  exit 1
}

echo "[deploy] OK — $(systemctl is-active "$SERVICE")"
code="000"
for _ in {1..10}; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/bounties || true)"
  [[ "$code" == "200" ]] && break
  sleep 1
done
echo "[deploy] GET http://127.0.0.1:3001/bounties -> HTTP $code"
[[ "$code" == "200" ]]
