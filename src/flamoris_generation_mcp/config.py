"""Environment-only deployment configuration; paths are local to the MCP process."""

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

ModelKind = Literal[
    "checkpoint",
    "lora",
    "vae",
    "controlnet",
    "clip",
    "clip_vision",
    "diffusion_model",
    "text_encoder",
    "unet",
]
MODEL_FOLDERS: dict[str, str] = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "controlnet": "controlnet",
    "clip": "clip",
    "clip_vision": "clip_vision",
    "diffusion_model": "diffusion_models",
    "text_encoder": "text_encoders",
    "unet": "unet",
}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comfyui_url: HttpUrl = HttpUrl("http://localhost:8188")
    model_root: Path = Path("models")
    model_dirs: dict[ModelKind, list[Path]] = Field(default_factory=dict)
    workflow_dir: Path = Path(".generation/workflows")
    workflow_definition_dir: Path = Path(".generation/definitions")
    output_dir: Path = Path(".generation/outputs")
    comfyui_output_root: Path | None = None
    provider_cleanup_enabled: bool = False
    provider_retention_days: int = Field(default=30, ge=0, le=36500)
    request_timeout: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    transfer_max_bytes: int = Field(default=1024**3, ge=1, le=4 * 1024**3)
    transfer_disk_bytes: int = Field(default=8 * 1024**3, ge=1, le=64 * 1024**3)
    targeted_interrupt: bool = False
    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    http_host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    http_port: int = Field(default=8765, ge=1, le=65535)
    mcp_path: str = Field(default="/mcp", max_length=256)

    @field_validator("http_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError("HTTP host must be a hostname or unbracketed IP address, not a URL")
        return value

    @field_validator("mcp_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if value == "/healthz":
            raise ValueError("MCP path /healthz is reserved for the HTTP liveness endpoint")
        if value != "/" and (
            not re.fullmatch(r"/[A-Za-z0-9_.~-]+(?:/[A-Za-z0-9_.~-]+)*", value)
            or any(segment in {".", ".."} for segment in value.split("/"))
        ):
            raise ValueError(
                "MCP path must be a literal absolute URL path without a trailing slash"
            )
        return value

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        fields = {
            "COMFYUI_URL": "comfyui_url",
            "MODEL_ROOT": "model_root",
            "WORKFLOW_DIR": "workflow_dir",
            "WORKFLOW_DEFINITION_DIR": "workflow_definition_dir",
            "OUTPUT_DIR": "output_dir",
            "COMFYUI_OUTPUT_ROOT": "comfyui_output_root",
            "PROVIDER_CLEANUP_ENABLED": "provider_cleanup_enabled",
            "PROVIDER_RETENTION_DAYS": "provider_retention_days",
            "REQUEST_TIMEOUT": "request_timeout",
            "TRANSFER_MAX_BYTES": "transfer_max_bytes",
            "TRANSFER_DISK_BYTES": "transfer_disk_bytes",
            "TARGETED_INTERRUPT": "targeted_interrupt",
            "MCP_TRANSPORT": "mcp_transport",
            "HTTP_HOST": "http_host",
            "HTTP_PORT": "http_port",
            "MCP_PATH": "mcp_path",
        }
        values = {
            field: os.environ["FLAMORIS_" + suffix]
            for suffix, field in fields.items()
            if "FLAMORIS_" + suffix in os.environ
        }
        if "FLAMORIS_MODEL_DIRS" in os.environ:
            values["model_dirs"] = json.loads(os.environ["FLAMORIS_MODEL_DIRS"])
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls.model_validate(values)

    def roots(self, kind: ModelKind) -> list[Path]:
        return self.model_dirs.get(kind, [self.model_root / MODEL_FOLDERS[kind]])
