import hashlib
import hmac
import re
import secrets
import time
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.config import get_settings
from app.models import Device

bearer = HTTPBearer(auto_error=False)
LOCAL_SESSION_COOKIE = "soundwatch_local_session"
LOCAL_SESSION_SECONDS = 8 * 60 * 60
LOCAL_REQUEST_HEADER = "x-soundwatch-local"


def local_request_origin(request: Request, *, issuing: bool = False) -> str | None:
    """Validate local browser intent independently of the socket peer.

    Docker forwards loopback connections through a gateway address. The host's
    loopback-only port binding is the network boundary; exact host/origin and
    fetch metadata checks prevent browser CSRF and DNS rebinding. Local mode
    must not be enabled behind a tunnel or reverse proxy.
    """
    settings = get_settings()
    if not settings.local_browser_access:
        return None
    headers = request.headers
    if any(name == "forwarded" or name.startswith("x-forwarded-") for name in headers):
        return None
    port = settings.local_browser_port
    hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
    if len(headers.getlist("host")) != 1 or headers.get("host") not in hosts:
        return None
    if request.url.scheme != "http":
        return None
    if headers.getlist(LOCAL_REQUEST_HEADER) != ["1"]:
        return None
    origin = f"http://{headers['host']}"
    origins = headers.getlist("origin")
    if origins and origins != [origin]:
        return None
    if (issuing or request.method not in {"GET", "HEAD", "OPTIONS"}) and origins != [origin]:
        return None
    fetch_sites = headers.getlist("sec-fetch-site")
    if fetch_sites and fetch_sites != ["same-origin"]:
        return None
    return origin


def _session_signature(value: str, origin: str) -> str:
    message = f"soundwatch-local-session\0{origin}\0{value}".encode()
    return hmac.new(get_settings().admin_token.encode(), message, hashlib.sha256).hexdigest()


def issue_local_session(request: Request, response: Response) -> None:
    origin = local_request_origin(request, issuing=True)
    if origin is None:
        raise HTTPException(403, "Local browser access is unavailable for this request")
    expires = int(time.time()) + LOCAL_SESSION_SECONDS
    value = f"v1.{expires}.{secrets.token_urlsafe(24)}"
    response.set_cookie(
        LOCAL_SESSION_COOKIE, f"{value}.{_session_signature(value, origin)}",
        max_age=LOCAL_SESSION_SECONDS, httponly=True, samesite="strict", path="/",
        # This mode deliberately serves HTTP on the loopback interface only.
        secure=False,
    )
    response.headers["Cache-Control"] = "no-store"


def has_local_session(request: Request) -> bool:
    origin = local_request_origin(request)
    if origin is None:
        return False
    cookie = request.cookies.get(LOCAL_SESSION_COOKIE, "")
    match = re.fullmatch(r"(v1\.(\d{1,12})\.[A-Za-z0-9_-]{32})\.([a-f0-9]{64})", cookie)
    if match is None:
        return False
    value, expires, signature = match.groups()
    remaining = int(expires) - time.time()
    if not 0 < remaining <= LOCAL_SESSION_SECONDS:
        return False
    return secrets.compare_digest(signature, _session_signature(value, origin))


def token_hash(token: str) -> str:
    # Tokens have 256 bits of entropy, making fast one-way hashing appropriate.
    return hashlib.sha256(token.encode()).hexdigest()


def token_value(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    return credentials.credentials


def is_admin(token: str) -> bool:
    return secrets.compare_digest(token, get_settings().admin_token)


def require_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    # An explicitly supplied bearer credential takes precedence, preserving
    # existing API clients and never masking an invalid token with a cookie.
    if credentials is not None and credentials.scheme.lower() == "bearer":
        if is_admin(credentials.credentials):
            return
        raise HTTPException(403, "Administrator permission required")
    if has_local_session(request):
        return
    raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})


def authenticate_device(db: Session, device_id, token: str) -> Device:
    device = db.get(Device, device_id)
    if device is None or not device.enabled or not secrets.compare_digest(device.credential_hash, token_hash(token)):
        raise HTTPException(401, "Invalid device credentials", headers={"WWW-Authenticate": "Bearer"})
    return device
