"""Optional device-only listener reusing the existing authenticated audio API.

It is never started by the default Compose stack. Local browser sessions and
management routes are not exposed here; no second ingestion implementation exists.
"""
import secrets

from fastapi.responses import JSONResponse

from app.config import get_settings
from app.main import create_app
from app.pcm_api import router as pcm_router


def create_device_app():
    application = create_app()
    application.state.local_audio_playback = False
    allowed = {
        ("/health/live", "GET"),
        ("/audio", "POST"),
        ("/audio/{chunk_id}", "GET"),
        ("/audio/{chunk_id}/file", "GET"),
    }
    application.router.routes = [route for route in application.router.routes
        if getattr(route, "methods", None)
        and all((route.path, method) in allowed for method in route.methods)]
    # Include the narrow adapter after filtering: recent FastAPI versions keep
    # include_router entries lazy, without exposing a path/method on the wrapper.
    application.include_router(pcm_router)
    application.openapi_url = application.docs_url = application.redoc_url = None
    application.openapi_schema = None

    @application.middleware("http")
    async def devices_only(request, call_next):
        # Even a valid administrator token should not be put into device firmware
        # or used for broad recording access through this optional LAN listener.
        authorization = request.headers.get("authorization", "").split()
        if (len(authorization) == 2 and authorization[0].lower() == "bearer"
                and secrets.compare_digest(authorization[1].encode(), get_settings().admin_token.encode())):
            return JSONResponse({"error": {"code": "device_credential_required",
                "message": "Use this device's credential on the device listener"}}, status_code=403)
        return await call_next(request)

    return application
