# Railway deployment

AssureX deploys as two independent Railway services from this repository. The browser never loads HTML or assets from Flask.

## API service

- Service name: `API`
- Root directory: `/`
- Railway config path: `/railway.json`
- Railpack config: `/railpack.json`
- Public healthcheck: `/api/health`

Required variables:

```dotenv
ASSUREX_ENV=production
DATABASE_URL=${{Postgres.DATABASE_URL}}
JWT_SECRET_KEY=<random secret of at least 32 bytes>
RATELIMIT_STORAGE_URI=${{Redis.REDIS_URL}}
FRONTEND_ORIGINS=https://${{Frontend.RAILWAY_PUBLIC_DOMAIN}}
```

Add the existing document-storage and OCR variables from `config/.env.example`. Generate a public domain for the API service. The API image installs Tesseract, applies database migrations on startup, and runs Waitress on Railway's `PORT`.

## Frontend service

- Service name: `Frontend`
- Root directory: `/frontend`
- Railway config path: `/frontend/railway.json`
- Railpack config: `/frontend/railpack.json`
- Public healthcheck: `/`

Required variable:

```dotenv
VITE_API_URL=https://${{API.RAILWAY_PUBLIC_DOMAIN}}/api
```

Generate a public domain for the frontend service. Railpack builds the Vite application into `dist/` and serves it with Caddy. `Staticfile` enables SPA fallback for `/products`, `/claims`, and `/dashboard`.

When using custom domains, replace both generated-domain references with the final public HTTPS origins. `FRONTEND_ORIGINS` accepts a comma-separated list for production and preview frontends; wildcard origins are rejected.

## Local development

Create `.env` at the repository root and `frontend/.env.local` from the provided examples. Then run:

```powershell
# Terminal 1, repository root
python -m flask --app backend:create_app db upgrade
waitress-serve --listen=127.0.0.1:8000 --call backend:create_app

# Terminal 2
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. The Vite app calls `http://127.0.0.1:8000/api` directly, and Flask allows only the configured development origins.
