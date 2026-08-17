from fastapi import FastAPI

from transport.routes import router
from config.logging import configure_logging

configure_logging()
app = FastAPI(
    title="Compliance AI Service",
    version="1.0.0",
)


@app.get("/health")
def health():
    return {
        "status": "UP"
    }

app.include_router(router)