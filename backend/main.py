"""Compose the local HTTP application; routes own their request handling."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import __version__, db
from .config import ROOT
from .engine import engine
from .export import ExportError
from .routes import resources, settings, tasks, subtitles


@asynccontextmanager
async def lifespan(app):
    db.init()
    yield
    engine.shutdown()


app = FastAPI(title="听澜字幕 Tinglan", version=__version__, lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
)


@app.middleware("http")
async def local_only(request, call_next):
    # 本机安全访问中间件：
    # 1. 来源校验：检查 sec-fetch-site 与 origin，仅允许同源本机页面访问，防御跨站请求伪造 (CSRF)；
    # 2. 请求头约束：所有数据变更请求 (POST/PUT/PATCH/DELETE) 必须附带 X-Tinglan: 1 自定义头；
    # 3. 安全响应头：强制配置 CSP、frame-ancestors 'none'、nosniff，阻止被第三方恶意网站内嵌调用。
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and origin != str(request.base_url).rstrip("/")
    ):
        return JSONResponse({"detail": "仅允许本机页面访问"}, status_code=403)
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and request.headers.get("x-tinglan") != "1"
    ):
        return JSONResponse({"detail": "缺少本机请求标记"}, status_code=403)
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        }
    )
    return response


@app.exception_handler(ExportError)
async def export_error(request, exc):
    return JSONResponse(
        {"detail": str(exc), "subtitle_id": exc.subtitle_id, "reason": exc.reason},
        status_code=400,
    )


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(KeyError)
async def missing(request, exc):
    return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)


for router in (resources.router, settings.router, tasks.router, subtitles.router):
    app.include_router(router)


@app.get("/")
def index():
    path = ROOT / "frontend" / "dist" / "index.html"
    if not path.exists():
        return Response("前端尚未构建，请运行 安装.cmd", status_code=503)
    return FileResponse(path)


assets = ROOT / "frontend" / "dist" / "assets"
if assets.exists():
    app.mount("/assets", StaticFiles(directory=assets), name="assets")



