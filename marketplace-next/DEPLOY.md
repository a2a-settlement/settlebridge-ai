# Deploying the marketplace on the production droplet

The site **https://market.settlebridge.ai** is served by **Caddy** → **Next.js standalone** on **127.0.0.1:3001**, not by the `frontend` service in `docker-compose.yml`.

| Piece | Role |
|-------|------|
| **Caddy** (`market.settlebridge.ai`) | `/api/*` and `/.well-known/*` → FastAPI **8002**; everything else → Next **3001** |
| **`settlebridge-marketplace.service`** | `node server.js` from `.next/standalone`, user `clawdbot`, `PORT=3001` |
| **`settlebridge.service`** | Gunicorn FastAPI on **127.0.0.1:8002** |

The **Vite** app in `../frontend` (and Docker Compose `frontend` on port 5173) is for **local dev only**. UI changes for production belong in **`marketplace-next/`**.

## One-command deploy (run as root on the droplet)

```bash
sudo /home/clawdbot/settlebridge-ai/marketplace-next/scripts/deploy-droplet.sh
```

This builds as `clawdbot`, copies standalone assets, fixes ownership, and restarts `settlebridge-marketplace`.

## Manual steps (if you prefer)

1. **Build** (must be able to write `.next/`; use `clawdbot`, not root):

   ```bash
   sudo -u clawdbot bash -lc 'cd /home/clawdbot/settlebridge-ai/marketplace-next && npm run build'
   ```

2. **Copy into standalone** (required after every `next build`; the service only reads `.next/standalone`):

   ```bash
   sudo -u clawdbot bash -lc 'cd /home/clawdbot/settlebridge-ai/marketplace-next && \
     rm -rf .next/standalone/.next/static .next/standalone/public && \
     cp -r .next/static .next/standalone/.next/static && \
     cp -r public .next/standalone/public'
   ```

3. **Ownership** (avoid EACCES if anything under `.next` was created as root):

   ```bash
   chown -R clawdbot:clawdbot /home/clawdbot/settlebridge-ai/marketplace-next/.next/standalone
   chown -R clawdbot:clawdbot /home/clawdbot/settlebridge-ai/marketplace-next/.next
   ```

4. **Restart**:

   ```bash
   systemctl restart settlebridge-marketplace.service
   ```

5. **Smoke test**:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3001/bounties
   ```

## Common failures

| Symptom | Cause | Fix |
|---------|--------|-----|
| `npm run build` → `EACCES` under `.next` | `.next` partly owned by **root** | `chown -R clawdbot:clawdbot .../marketplace-next/.next` |
| Site 404 / missing JS chunks | Forgot to copy **`.next/static`** into **standalone** | Run step 2 above, then restart |
| Changes in `frontend/` never show on market | Production is **Next**, not Vite | Edit `marketplace-next/`, deploy from this doc |
| API works, UI old | Backend restarted but not Next | `systemctl restart settlebridge-marketplace` |

## Environment

- Systemd: `systemctl cat settlebridge-marketplace.service`
- Next reads **`INTERNAL_API_BASE_URL=http://127.0.0.1:8002/api`** for server-side API calls (set in the unit or env as applicable).
