from contextlib import asynccontextmanager
import asyncio
import base64
from datetime import datetime
import html
import ipaddress
import re
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from receipt.config import Settings, load_settings
from receipt.mailer import run_worker
from receipt.store import RateLimited, Store

# One immutable RGBA PNG, shared under unique per-link filenames.
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=")
LINK_PATTERN = re.compile(r"^[0-9a-f]{48}$")
CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                 "Pragma": "no-cache", "Expires": "0", "X-Content-Type-Options": "nosniff"}


class CreateLink(BaseModel):
    notification_email: str = Field(max_length=254)
    note: str = Field(default="", max_length=500)

    @field_validator("notification_email")
    @classmethod
    def valid_email(cls, value):
        try:
            email = validate_email(value, check_deliverability=False)
        except EmailNotValidError as exc:
            raise ValueError(str(exc)) from exc
        if not email.ascii_email:
            raise ValueError("Use an ASCII email mailbox")
        return email.ascii_email


def source_info(request: Request, settings: Settings):
    peer = request.client.host if request.client else "unknown"
    networks = [ipaddress.ip_network(item) for item in settings.trusted_proxies]

    def trusted(value):
        try:
            address = ipaddress.ip_address(value)
            return any(address in network for network in networks)
        except ValueError:
            return False

    effective = peer
    if trusted(peer):
        forwarded = request.headers.get("x-forwarded-for", "")
        chain = [value.strip() for value in forwarded.split(",") if value.strip()]
        try:
            for value in chain:
                ipaddress.ip_address(value)
        except ValueError:
            chain = []
        for value in reversed(chain):
            effective = value
            if not trusted(value):
                break
    return {"connection_ip": peer, "connection_port": request.client.port if request.client else None,
            "effective_source_ip": effective, "peer_is_trusted_proxy": trusted(peer)}


def create_app(settings: Settings | None = None, start_worker=True) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings.database)

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        task = asyncio.create_task(run_worker(store, settings, stop)) if start_worker else None
        try:
            yield
        finally:
            stop.set()
            if task:
                await task

    app = FastAPI(title="邮件图片请求通知", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings

    def authorize(link_id, secret):
        if not LINK_PATTERN.fullmatch(link_id) or not secret or len(secret) > 200:
            raise HTTPException(404, "Link not found or invalid management token")
        link = store.authorized(link_id, secret)
        if not link:
            raise HTTPException(404, "Link not found or invalid management token")
        return link

    @app.get("/health")
    def health():
        return {"status": "ok", "smtp_configured": settings.smtp_enabled}

    @app.post("/api/links", status_code=201)
    def create_link(payload: CreateLink, request: Request):
        source = source_info(request, settings)
        try:
            link_id, secret = store.create(payload.notification_email, payload.note,
                                          source["effective_source_ip"], settings.create_limit,
                                          settings.create_window_seconds)
        except RateLimited:
            raise HTTPException(429, "Creation limit reached; try again later",
                                headers={"Retry-After": str(settings.create_window_seconds)})
        url = settings.public_base_url.rstrip("/") + f"/pixel/{link_id}.png"
        return {"id": link_id, "pixel_url": url, "management_token": secret,
                "html": f'<img src="{html.escape(url, quote=True)}" width="1" height="1" alt="" style="width:1px;height:1px;border:0;">'}

    @app.api_route("/pixel/{filename}", methods=["GET", "HEAD"])
    def pixel(filename: str, request: Request):
        link_id = filename.removesuffix(".png")
        if not filename.endswith(".png") or not LINK_PATTERN.fullmatch(link_id):
            raise HTTPException(404, "Unknown image", headers=CACHE_HEADERS)
        details = {
            "received_at": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
            "method": request.method,
            "http_version": request.scope.get("http_version"),
            "scheme_seen_by_app": request.scope.get("scheme"),
            "path": request.url.path,
            "raw_path": request.scope.get("raw_path", b"").decode("latin-1"),
            "raw_query_string": request.scope.get("query_string", b"").decode("latin-1"),
            "headers": [[key.decode("latin-1"), value.decode("latin-1")]
                        for key, value in request.scope["headers"]],
            **source_info(request, settings),
        }
        event = store.record(link_id, details, settings.retry_hours)
        if not event:
            raise HTTPException(404, "Unknown or inactive image", headers=CACHE_HEADERS)
        return Response(content=PIXEL if request.method == "GET" else b"",
                        media_type="image/png",
                        headers={**CACHE_HEADERS, "Content-Length": str(len(PIXEL))})

    @app.get("/api/links/{link_id}")
    def status(link_id: str, x_management_token: str = Header(default="")):
        link = authorize(link_id, x_management_token)
        return {"id": link_id, "active": bool(link["active"]), **store.events(link_id)}

    @app.post("/api/links/{link_id}/stop")
    def stop_link(link_id: str, x_management_token: str = Header(default="")):
        authorize(link_id, x_management_token)
        store.stop(link_id)
        return {"active": False}

    @app.post("/api/links/{link_id}/retry")
    def retry(link_id: str, x_management_token: str = Header(default="")):
        link = authorize(link_id, x_management_token)
        if not link["active"]:
            raise HTTPException(409, "Inactive link cannot retry notifications")
        return {"requeued": store.retry(link_id, settings.retry_hours)}

    @app.delete("/api/links/{link_id}")
    def delete(link_id: str, x_management_token: str = Header(default="")):
        authorize(link_id, x_management_token)
        store.delete(link_id)
        return {"deleted": True}

    return app


def main():
    import uvicorn
    uvicorn.run("receipt.server:create_app", factory=True, host="0.0.0.0", port=8000,
                workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
