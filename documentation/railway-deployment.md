# Railway deployment

AssureX deploys as two independent Railway services from this repository. The browser never loads HTML or assets from Flask.

## API service

- Service name: `API`
- Root directory: repository root (leave Railway's **Root Directory** setting empty; do not set it to `backend`)
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
FRONTEND_URL=https://${{Frontend.RAILWAY_PUBLIC_DOMAIN}}
REPORT_WORKER_ENABLED=true
REPORT_STORAGE_PATH=/app/instance/reports
```

Add the existing document-storage and OCR variables from `config/.env.example`. Generate a public domain for the API service. The API image installs Tesseract, applies database migrations, starts the report worker with model preloading disabled, and runs Waitress on Railway's `PORT`. Attach a private persistent volume that contains `REPORT_STORAGE_PATH` if completed report downloads must survive redeploys. Keep the API at one replica while reports use local filesystem storage; multiple replicas need genuinely shared report storage.

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

Generate a public domain for the frontend service. Railpack builds the Vite application into `dist/` and serves it with Caddy. `Staticfile` enables SPA fallback for every frontend route, including `/products`, `/claims`, `/dashboard`, `/reports`, and `/search`.

When using custom domains, replace the generated-domain references with the final public HTTPS origins. `FRONTEND_URL` is the canonical browser destination. `FRONTEND_ORIGINS` accepts a comma-separated list for production and preview frontends; wildcard origins are rejected.

## Local development

Create `.env` at the repository root and `frontend/.env.local` from the provided examples. Then run:

```powershell
# Terminal 1, repository root
python -m flask --app backend:create_app db upgrade
python -m flask --app backend:create_app run --host=127.0.0.1 --port=8000

# Terminal 2
cd frontend
npm ci
npm run dev

# Terminal 3, repository root, when testing report exports
python -m flask --app backend:create_app report-worker
```

Open `http://localhost:5173`. The Vite app calls `http://127.0.0.1:8000/api` directly, and Flask allows only the configured development origins.

The Flask CLI loads `.env` automatically. If Waitress is used locally instead, export the values from `.env` into the process environment before starting it; Railway already injects those variables into the API service.


## Report worker behavior

The Railway API start script runs the database-backed report worker in the same service as the web API by default. This keeps queued exports from stalling and ensures the worker and download endpoint see the same local report files. The worker is started with `MODEL_PRELOAD_ENABLED=false` and `MODEL_PRELOAD_STRICT=false`, avoiding a second in-memory copy of the Python and GTM models.

Set `REPORT_WORKER_ENABLED=false` only if a separately supervised worker is configured against the same database and genuinely shared `REPORT_STORAGE_PATH`. If local filesystem report storage is used, do not horizontally scale the API service because a request may reach a replica that does not own the generated artifact.
