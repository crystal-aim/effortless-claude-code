"""Admin API for MLX local inference server management."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import require_admin
from app.backend import mlx_server
from app.config import get_config
from app.models import User

router = APIRouter(prefix="/api/mlx", tags=["admin:mlx"])


class MlxStartIn(BaseModel):
    model: str


@router.get("/status")
def mlx_status(_admin: User = Depends(require_admin)):
    return mlx_server.get_status()


@router.get("/models")
def mlx_models(_admin: User = Depends(require_admin)):
    cfg = get_config()
    model_map = cfg.backend.mlx.model_map
    models = []
    for short_name, hf_id in model_map.items():
        models.append({
            "name": short_name,
            "hf_id": hf_id,
            "downloaded": mlx_server.is_model_downloaded(hf_id),
        })
    return {"models": models}


@router.post("/start")
def mlx_start(body: MlxStartIn, _admin: User = Depends(require_admin)):
    cfg = get_config()
    model_map = cfg.backend.mlx.model_map
    hf_id = model_map.get(body.model)
    if hf_id is None:
        return {"error": f"Unknown model: {body.model}. Available: {list(model_map.keys())}"}

    mlx_server.start_server(hf_id, port=cfg.backend.mlx.port, display_name=body.model)
    return {"started": True, "model": body.model, "hf_id": hf_id}


@router.post("/stop")
def mlx_stop(_admin: User = Depends(require_admin)):
    mlx_server.stop_server()
    return {"stopped": True}


@router.get("/logs")
def mlx_logs(n: int = 100, _admin: User = Depends(require_admin)):
    return {"logs": mlx_server.get_logs(n)}
