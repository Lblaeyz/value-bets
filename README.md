# Value Bets

Football value-betting analytics powered by a Dixon–Coles Poisson model. The
system collects fixtures, historical results, team data, and bookmaker odds;
calculates outcome probabilities and expected value; and presents the
recommendations in a React dashboard.

## Repository structure

```text
.
├── backend/                 # FastAPI service — deploy to Railway
│   ├── app/
│   │   ├── api/             # Health, matches, predictions, performance, admin
│   │   ├── db/              # Supabase client and database queries
│   │   ├── ingestion/       # Football-data.org, OpenLigaDB, Odds API, API-Football
│   │   ├── models/          # Dixon–Coles Poisson model and EV calculator
│   │   ├── scheduler/       # Daily pipeline and scheduled jobs
│   │   └── utils/
│   ├── Procfile
│   ├── railway.json
│   └── requirements.txt
├── frontend/                # React + Vite dashboard — deploy to Vercel
│   ├── src/
│   ├── public/
│   ├── package.json
│   ├── vercel.json
│   └── vite.config.ts
└── README.md
```

The two applications are intentionally independent. They have separate
dependency manifests and can be deployed from the same GitHub repository by
setting the hosting platform's root directory:

- Railway root directory: `backend`
- Vercel root directory: `frontend`

## Stack

### Backend

- Python 3.11+
- FastAPI and Uvicorn
- APScheduler for the daily pipeline
- Supabase PostgreSQL via `supabase-py`
- NumPy, SciPy, and pandas
- Dixon–Coles Poisson football model
- Sentry-compatible error monitoring

### Frontend

- React 19
- TypeScript
- Vite
- Tailwind CSS
- Radix UI primitives
- Recharts
- TanStack React Query
- Wouter routing

### Data providers

- [football-data.org](https://www.football-data.org/) for major competition fixtures
- [OpenLigaDB](https://www.openligadb.de/) for German 2. Bundesliga and 3. Liga
- [The Odds API](https://the-odds-api.com/) for bookmaker markets
- API-Football for optional match enrichment
- Supabase for persistent storage

## Local development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PORT=8000
uvicorn app.main:app --host 0.0.0.0 --port $PORT --reload
```

Required backend environment variables:

```text
SUPABASE_URL=
SUPABASE_KEY=
FOOTBALL_DATA_API_KEY=
ODDS_API_KEY=
API_FOOTBALL_KEY=
```

Optional configuration includes `ADMIN_API_KEY`, `SENTRY_DSN`,
`API_FOOTBALL_DAILY_LIMIT`, `ODDS_API_MONTHLY_LIMIT`, `MIN_DATA_QUALITY`,
`MIN_VALUE_EDGE`, and `MIN_CONFIDENCE`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` to the backend origin when the API is not served from the
same origin:

```text
VITE_API_URL=https://your-railway-service.up.railway.app
```

The frontend appends its `/api/...` paths to that origin. For local development
with the backend on port 8000, use `VITE_API_URL=http://localhost:8000`.

## Deployment

### Railway — backend

1. Create a Railway service from this GitHub repository.
2. Set the service root directory to `backend`.
3. Railway uses `backend/railway.json` and starts:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```

4. Add the required API keys and Supabase variables in Railway service
   variables.
5. Confirm `GET /api/healthz` responds successfully.

### Vercel — frontend

1. Import the same GitHub repository into Vercel.
2. Set the project root directory to `frontend`.
3. Vercel uses `frontend/vercel.json`, runs `npm run build`, and serves
   `frontend/dist`.
4. Add `VITE_API_URL` with the public Railway backend URL.
5. Redeploy after changing environment variables because Vite embeds
   `VITE_*` values at build time.

## API overview

- `GET /api/healthz` — service health
- `GET /api/matches/today` — today's fixtures and value bets
- `GET /api/matches/date/{date}` — fixtures for a specific date
- `GET /api/matches/{id}` — match detail
- `GET /api/predictions` — paginated predictions
- `GET /api/predictions/recommended` — recommended value bets
- `GET /api/performance/summary` — performance summary
- `POST /api/admin/run-pipeline` — run the daily pipeline
- `POST /api/admin/backfill` — backfill a historical date

## Important note

This software provides statistical analysis, not financial advice. Betting
involves risk; use appropriate limits and comply with local laws.