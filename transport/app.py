from fastapi import FastAPI

from transport.routes import router


app = FastAPI(
    title="Compliance AI Service",
    version="1.0.0",
)

app.include_router(router)