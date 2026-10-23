from fastapi import FastAPI

from app.api.routes import auth, companies, factors, records
from app.core.errors import register_error_handlers

app = FastAPI(title="EmissionCore API")
register_error_handlers(app)

app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(factors.router)
app.include_router(records.router)


@app.get("/health")
def health():
    return {"status": "ok"}
