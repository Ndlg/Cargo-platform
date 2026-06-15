# Backend

FastAPI backend scaffold for the waybill-driven reporting platform.

## Local run

```powershell
cd backend
python -m venv .venv
..\.venv\Scripts\python -m pip install -r requirements.txt
..\.venv\Scripts\python -m uvicorn app.main:app --reload
```

OpenAPI is available at `http://127.0.0.1:8000/docs`.

The backend reads the repository root `.env` file on startup. For normal local
development, initialize MySQL with `scripts/init_db.sql` and keep
`AUTO_CREATE_TABLES=false`.

For foundation tests or quick local experiments without MySQL, use SQLite:

```powershell
$env:DATABASE_URL="sqlite:///./local-dev.db"
$env:AUTO_CREATE_TABLES="true"
$env:SECRET_KEY="dev-secret"
python -m uvicorn app.main:app --reload
```

Run backend tests:

```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests -q
```
