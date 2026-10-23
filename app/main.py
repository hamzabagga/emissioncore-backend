from fastapi import FastAPI

app = FastAPI(title="EmissionCore API")


@app.get("/health")
def health():
    return {"status": "ok"}