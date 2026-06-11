import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


def _frontend_root() -> Path | None:
    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path(os.getenv("FRONTEND_DIST_DIR", "")) if os.getenv("FRONTEND_DIST_DIR") else None,
        Path(__file__).resolve().parent / "static",
        project_root / "public",
        project_root / "frontend" / "dist",
    ]
    for candidate in candidates:
        if candidate and (candidate / "index.html").is_file():
            return candidate
    return None


_static_root = _frontend_root()
if _static_root:
    _assets_root = _static_root / "assets"
    if _assets_root.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets_root), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(_static_root / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_fallback(path: str):
        if path == "health" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        requested_file = _static_root / path
        if requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(_static_root / "index.html")
