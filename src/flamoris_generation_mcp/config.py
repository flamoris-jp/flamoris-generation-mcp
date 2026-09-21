"""Environment-only deployment configuration; paths are local to the MCP process."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

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
    output_dir: Path = Path(".generation/outputs")
    request_timeout: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    targeted_interrupt: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        fields = {
            "COMFYUI_URL": "comfyui_url",
            "MODEL_ROOT": "model_root",
            "WORKFLOW_DIR": "workflow_dir",
            "OUTPUT_DIR": "output_dir",
            "REQUEST_TIMEOUT": "request_timeout",
            "TARGETED_INTERRUPT": "targeted_interrupt",
        }
        values = {
            field: os.environ["FLAMORIS_" + suffix]
            for suffix, field in fields.items()
            if "FLAMORIS_" + suffix in os.environ
        }
        if "FLAMORIS_MODEL_DIRS" in os.environ:
            values["model_dirs"] = json.loads(os.environ["FLAMORIS_MODEL_DIRS"])
        return cls.model_validate(values)

    def roots(self, kind: ModelKind) -> list[Path]:
        return self.model_dirs.get(kind, [self.model_root / MODEL_FOLDERS[kind]])
