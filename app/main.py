from fastapi import FastAPI

from app.api.routes import auth
from app.core.errors import register_error_handlers

app = FastAPI(title="EmissionCore API")
register_error_handlers(app)

app.include_router(auth.router)


@app.get("/health")
def health():
    return {"status": "ok"}