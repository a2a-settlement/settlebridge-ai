# SettleBridge Marketplace (Next.js)

Production marketplace UI for **market.settlebridge.ai**: App Router, ISR/SSR for SEO (`generateMetadata`, JSON-LD, `sitemap.xml`, `robots.txt`), with client islands for interactive flows.

## Dev

```bash
npm install
# Point at local FastAPI (default Vite proxy port)
export INTERNAL_API_BASE_URL=http://127.0.0.1:8002/api
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Browser calls use same-origin `/api/*` (Caddy proxies to FastAPI in production).

## Production build

See [deploy/README.md](deploy/README.md) for systemd + Caddy + standalone `server.js` layout.

## Legacy UI

The Vite marketplace build remains under `../frontend` (`npm run build:marketplace`) until cutover is complete.
