from fastapi import FastAPI

from app.api.routes import audit, auth, companies, factors, imports, jobs, records, summary, users
from app.core.errors import register_error_handlers

app = FastAPI(title="EmissionCore API")
register_error_handlers(app)

app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(factors.router)
app.include_router(records.router)
app.include_router(users.router)
app.include_router(summary.router)
app.include_router(imports.router)
app.include_router(jobs.router)
app.include_router(audit.router)


@app.get("/health")
def health():
    return {"status": "ok"}