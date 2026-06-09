import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router


app = FastAPI(
    title="Competitor Analysis Agent System",
    description="Evidence-first competitor analysis with real provider and fallback modes.",
    version="0.1.0",
)

default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]
configured_origins = [item.strip() for item in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if item.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[*default_origins, *configured_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "mode": "provider-configured-langgraph"}


app.include_router(router, prefix="/api")
