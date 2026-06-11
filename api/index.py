import sys
import traceback
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

app = FastAPI(title="Competitor Analysis Agent System")

try:
    from app.main import app as backend_app  # noqa: E402

    app = backend_app
except Exception as exc:  # pragma: no cover - deployment diagnostics only
    _error = {
        "status": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "backend_path": str(BACKEND),
        "traceback": traceback.format_exc().splitlines()[-12:],
    }

    @app.get("/health")
    def health():
        return _error
