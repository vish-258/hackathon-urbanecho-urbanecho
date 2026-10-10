"""Same-origin application assets and explicit backend capabilities."""
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from app.auth import issue_local_session, require_admin
from app.config import get_settings

router = APIRouter()
STATIC = Path(__file__).with_name("static") / "application"
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; media-src 'self' blob:; "
        "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


@router.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/app", status_code=307)


@router.get("/app", include_in_schema=False)
@router.get("/app/", include_in_schema=False)
def application():
    return FileResponse(STATIC / "index.html", headers=HEADERS)


@router.post("/app/session", tags=["application"])
def local_session(request: Request, response: Response):
    """Open the private local workspace without exposing its server credential."""
    issue_local_session(request, response)
    return {"access": "local"}


@router.get("/app/{asset:path}", include_in_schema=False)
def application_asset(asset: str):
    root = STATIC.resolve()
    candidate = (root / asset).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.suffix not in {
        ".js", ".mjs", ".css", ".svg", ".png", ".jpg", ".webp", ".woff", ".woff2", ".html",
    }:
        raise HTTPException(404, "Asset not found")
    media_type = "text/javascript" if candidate.suffix in {".js", ".mjs"} else None
    return FileResponse(candidate, media_type=media_type, headers=HEADERS)


@router.get("/capabilities", tags=["application"])
def capabilities(admin: Annotated[None, Depends(require_admin)]):
    return {
        "daily_summaries": {
            "available": True,
            "generation_available": True,
            "endpoint": "/daily-summaries",
            "generation_endpoint": "/daily-summaries/generate",
            "reason": "Saved local-calendar-day summaries use sound-energy and duration weighting. "
                      "Coverage shows only usable recording time; simulated data is kept separate.",
        },
        "measurement_methods": [
            {"id": "spl_z_leq", "label": "SPL(Z)", "weighting": "Z", "calibration_required": True},
            {"id": "dbfs_rms", "label": "dBFS RMS", "weighting": "none", "calibration_required": False},
        ],
        "live_updates": {"transport": "sse", "snapshot": "/locations/status", "stream": "/events/stream",
                         "freshness_seconds": get_settings().live_freshness_seconds},
    }
