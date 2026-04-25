import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.admin import auth_routes, keys, stats
from app.admin import acc_routes, bedrock_routes, ecc_routes, mlx_routes
from app.auth import hash_password
from app.config import apply_db_overrides, get_config, load_config
from app.db import init_db, session_scope
from app.models import User
from app.proxy import router as proxy_router
from app.rate_limit import limiter

log = logging.getLogger("ccm.main")


def _seed_admin() -> None:
    with session_scope() as db:
        if db.query(User).first() is not None:
            return
        email = os.environ.get("CCM_ADMIN_EMAIL")
        password = os.environ.get("CCM_ADMIN_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                "No admin user exists yet. Set CCM_ADMIN_EMAIL and CCM_ADMIN_PASSWORD "
                "in the environment to seed the initial admin account."
            )
        db.add(User(email=email, password_hash=hash_password(password), role="admin"))


def create_app() -> FastAPI:
    logging.basicConfig(
        level=os.environ.get("CCM_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_config(os.environ.get("CCM_CONFIG", "config.yaml"))
    init_db()
    apply_db_overrides(get_config())
    _seed_admin()

    from contextlib import asynccontextmanager
    from app.backend import mlx_server
    from app.ecc import auto_sync

    def _auto_start_mlx() -> None:
        try:
            last_model = mlx_server._db_load_last_selected()
            if not last_model:
                log.info("mlx auto-start: no last model saved, skipping")
                return
            cfg = get_config()
            hf_id = cfg.backend.mlx.model_map.get(last_model)
            if hf_id is None:
                log.warning("mlx auto-start: model %r not in model_map, skipping", last_model)
                return
            log.info("mlx auto-start: resuming %s (%s)", last_model, hf_id)
            mlx_server.start_server(hf_id, port=cfg.backend.mlx.port, display_name=last_model)
        except Exception:
            log.exception("mlx auto-start failed (non-fatal)")

    @asynccontextmanager
    async def lifespan(app):
        import asyncio as _asyncio
        auto_sync.bind_loop(_asyncio.get_running_loop())
        auto_sync.start()
        _auto_start_mlx()
        mlx_server.start_watchdog()
        yield
        mlx_server.stop_watchdog()
        auto_sync.stop()
        mlx_server.stop_server()

    app = FastAPI(title="Claude Proxy", version="0.2.0", lifespan=lifespan)

    app.state.limiter = limiter

    def _rate_limit_handler(request, exc: RateLimitExceeded):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {exc.detail}"},
        )

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

    app.include_router(proxy_router)
    app.include_router(auth_routes.router)
    app.include_router(keys.router)
    app.include_router(keys.admin_keys_router)
    app.include_router(stats.router)
    app.include_router(stats.admin_router)
    app.include_router(bedrock_routes.router)
    app.include_router(mlx_routes.router)
    app.include_router(ecc_routes.router)
    app.include_router(acc_routes.router)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def root():
        return FileResponse(os.path.join(static_dir, "landing.html"))

    @app.get("/ui/landing")
    def ui_landing():
        return FileResponse(os.path.join(static_dir, "landing.html"))

    @app.get("/ui/login")
    def ui_login():
        return FileResponse(os.path.join(static_dir, "login.html"))

    @app.get("/ui/dashboard")
    def ui_dashboard():
        return RedirectResponse(url="/ui/admin")

    @app.get("/ui/admin")
    def ui_admin():
        return FileResponse(os.path.join(static_dir, "admin.html"))

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(os.path.join(static_dir, "favicon.svg"), media_type="image/svg+xml")

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from app.config import get_config

    cfg = get_config()
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)
