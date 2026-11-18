# Marketplace Next.js deployment (systemd + reverse proxy)

This is a generic recipe. Replace the placeholders (`settlebridge` service user, `/opt/settlebridge-ai` checkout path, `market.example.com` hostname) with values for your own host.

1. Build after `git pull`:

 ```bash
   cd /opt/settlebridge-ai/marketplace-next
   npm ci
   npm run build
   cp -r public .next/standalone/public
   mkdir -p .next/standalone/.next
   cp -r .next/static .next/standalone/.next/static
   ```

2. Install systemd unit (once):

   ```bash
   # Edit User= and WorkingDirectory= in the unit file first.
   sudo cp deploy/settlebridge-marketplace.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now settlebridge-marketplace.service
   ```

3. **Reverse proxy (Caddy shown)** — point your marketplace hostname at the Node server (after `/api/*` to FastAPI):

   ```caddy
   market.example.com {
     reverse_proxy /api/* 127.0.0.1:8002
     reverse_proxy /* 127.0.0.1:3001
   }
   ```

   Remove `file_server` / `root` for the old Vite static site.

4. Verify: `curl -sI http://127.0.0.1:3001/bounties | head -5`

The process cwd must be `.next/standalone` (see systemd `WorkingDirectory`) so `server.js` resolves bundled paths correctly.
