"""Trusted template builders and saved parameter recipes, not a raw node editing API."""

import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ModelCatalog, model_name

Template = Literal["text-to-image", "text-to-image-lora"]
MAX_RECIPE_BYTES = 256 * 1024
TEMPLATES = [
    {"id": "text-to-image", "description": "Checkpoint-based text-to-image; no LoRAs"},
    {"id": "text-to-image-lora", "description": "Text-to-image with an ordered LoRA chain"},
]


class Lora(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    strength_model: float = Field(default=1, ge=-20, le=20, allow_inf_nan=False)
    strength_clip: float = Field(default=1, ge=-20, le=20, allow_inf_nan=False)

    _name = field_validator("name")(model_name)


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    checkpoint: str
    positive_prompt: str = Field(min_length=1, max_length=20000)
    negative_prompt: str = Field(default="", max_length=20000)
    width: int = Field(default=512, ge=64, le=4096, multiple_of=8, strict=True)
    height: int = Field(default=512, ge=64, le=4096, multiple_of=8, strict=True)
    seed: int = Field(default=0, ge=0, le=2**64 - 1, strict=True)
    steps: int = Field(default=20, ge=1, le=150, strict=True)
    cfg: float = Field(default=7, ge=0, le=100, allow_inf_nan=False)
    sampler: str = Field(default="euler", pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    scheduler: str = Field(default="normal", pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    denoise: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    loras: Annotated[tuple[Lora, ...], Field(max_length=16)] = ()

    _checkpoint = field_validator("checkpoint")(model_name)


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    template: Template
    parameters: Parameters


def build_prompt(recipe: Recipe, catalog: ModelCatalog) -> dict:
    p = recipe.parameters
    catalog.require("checkpoint", p.checkpoint)
    if recipe.template == "text-to-image" and p.loras:
        raise ValueError("Use text-to-image-lora for LoRAs")
    if recipe.template == "text-to-image-lora" and not p.loras:
        raise ValueError("text-to-image-lora requires at least one LoRA")
    nodes = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": p.checkpoint}}}
    model, clip = ["1", 0], ["1", 1]
    for index, lora in enumerate(p.loras, start=10):
        catalog.require("lora", lora.name)
        key = str(index)
        nodes[key] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": model,
                "clip": clip,
                "lora_name": lora.name,
                "strength_model": lora.strength_model,
                "strength_clip": lora.strength_clip,
            },
        }
        model, clip = [key, 0], [key, 1]
    nodes.update(
        {
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": p.positive_prompt, "clip": clip},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": p.negative_prompt, "clip": clip},
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": p.width, "height": p.height, "batch_size": 1},
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": model,
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                    "seed": p.seed,
                    "steps": p.steps,
                    "cfg": p.cfg,
                    "sampler_name": p.sampler,
                    "scheduler": p.scheduler,
                    "denoise": p.denoise,
                },
            },
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {
                "class_type": "SaveImage",
                "inputs": {"images": ["6", 0], "filename_prefix": "flamoris"},
            },
        }
    )
    return nodes


def checked_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid workflow or job ID")
    return value


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class WorkflowStore:
    def __init__(self, catalog: ModelCatalog, directory: Path):
        self.catalog = catalog
        self.directory = directory
        self._recipes: dict[str, Recipe] = {}

    def build(self, template: Template, parameters: Parameters) -> dict:
        recipe = Recipe(template=template, parameters=parameters)
        prompt = build_prompt(recipe, self.catalog)
        if len(self._recipes) >= 1024:
            raise ValueError("Workflow session is full; save needed workflows and restart")
        workflow_id = uuid4().hex
        self._recipes[workflow_id] = recipe
        return {"workflow_id": workflow_id, **recipe.model_dump(mode="json"), "prompt": prompt}

    def get(self, workflow_id: str) -> Recipe:
        checked_id(workflow_id)
        if workflow_id in self._recipes:
            return self._recipes[workflow_id]
        path = self.directory / f"{workflow_id}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unknown workflow ID")
        if path.stat().st_size > MAX_RECIPE_BYTES:
            raise ValueError("Saved workflow is too large")
        return Recipe.model_validate_json(path.read_bytes())

    def save(self, workflow_id: str) -> dict:
        recipe = self.get(workflow_id)
        path = self.directory / f"{workflow_id}.json"
        content = recipe.model_dump_json(indent=2).encode()
        if len(content) > MAX_RECIPE_BYTES:
            raise ValueError("Saved workflow is too large")
        atomic_write(path, content)
        return {"workflow_id": workflow_id, "file": str(path), "saved": True}

    def list(self) -> dict:
        saved = []
        for path in sorted(self.directory.glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{32}", path.stem) and not path.is_symlink():
                saved.append(path.stem)
        return {
            "templates": TEMPLATES,
            "built_workflows": list(self._recipes),
            "saved_workflows": saved,
        }
