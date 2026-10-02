"""Trusted template builders and saved parameter recipes, not a raw node editing API."""

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ModelCatalog, model_name
from .workflow_registry import WorkflowRegistry

Template = Literal["text-to-image", "text-to-image-lora"]
MAX_RECIPE_BYTES = 256 * 1024
MAX_DISCOVERY_BYTES = 256 * 1024
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


class ExternalRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[2] = 2
    template: str
    definition_version: int
    definition_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    require_ready: bool = Field(default=False, strict=True)
    parameters: dict[str, Any]


AnyRecipe = Recipe | ExternalRecipe


def builtin_descriptors() -> list[dict]:
    """Graph-free descriptions of the existing builtin contracts."""
    properties = Parameters.model_json_schema()["properties"]
    parameters = {}
    for name, schema in properties.items():
        if name == "loras":
            continue
        spec = {
            k: v
            for k, v in schema.items()
            if k in {"type", "default", "minimum", "maximum", "minLength", "maxLength", "pattern"}
        }
        for source, target in (("minLength", "min_length"), ("maxLength", "max_length")):
            if source in spec:
                spec[target] = spec.pop(source)
        if "multipleOf" in schema:
            spec["multiple_of"] = int(schema["multipleOf"])
        parameters[name] = {
            **spec,
            "role": name,
            "required": name in properties and "default" not in schema,
        }
    parameters["checkpoint"]["model_kind"] = "checkpoint"
    loras = {
        "type": "ordered_loras",
        "role": "loras",
        "max_items": 16,
        "model_kind": "lora",
        "items": {
            "name": {"type": "string", "model_kind": "lora"},
            "strength_model": {"type": "number", "minimum": -20, "maximum": 20, "default": 1},
            "strength_clip": {"type": "number", "minimum": -20, "maximum": 20, "default": 1},
        },
    }
    return [
        {
            **template,
            "kind": "builtin",
            "metadata_schema_version": 2,
            "version": 1,
            "name": template["id"],
            "provider_id": "comfyui",
            "capability_id": "image.generate",
            "image": {
                "profile": "image-v1",
                "mode": "txt2img",
                "dimensions": {"mode": "parameters"},
            },
            "readiness": {
                "state": "ready",
                "basis": "builtin_compatibility",
                "descriptor_revision": 1,
            },
            "parameters": {
                **parameters,
                "loras": {
                    **loras,
                    "min_items": 1 if template["id"].endswith("-lora") else 0,
                    "max_items": 16 if template["id"].endswith("-lora") else 0,
                    "required": template["id"].endswith("-lora"),
                },
            },
        }
        for template in TEMPLATES
    ]


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
    def __init__(
        self,
        catalog: ModelCatalog,
        directory: Path,
        definition_dir: Path | None = None,
        *,
        v3_enabled=False,
    ):
        self.catalog = catalog
        self.directory = directory
        self.registry = WorkflowRegistry(definition_dir, catalog) if definition_dir else None
        self._recipes: dict[str, AnyRecipe] = {}
        self.readiness = None
        self.v3 = None
        if v3_enabled:
            if self.registry is None:
                raise ValueError("Image v3 requires the existing definition registry")
            from .image_v3 import ImageV3

            self.v3 = ImageV3(self)

    def definition_lock(self, recipe):
        if recipe.schema_version == 3:
            if self.v3 is None:
                raise ValueError("Workflow v3 is disabled")
            return self.v3.versions.lock
        return self.registry._register_lock

    def build_v3(self, workflow_id, version, digest, parameters, require_ready=True):
        if self.v3 is None or type(version) is not int or type(require_ready) is not bool:
            raise ValueError("Image v3 is disabled or its exact build contract is invalid")
        from .image_v3 import Identity

        Identity(id=workflow_id, version=version, digest=digest)
        with self.v3.versions.lock:
            recipe, definition = self.v3.materialize(workflow_id, version, digest, parameters)
            recipe = recipe.model_copy(update={"require_ready": require_ready})
            self.capture(recipe)
            if len(self._recipes) >= 1024:
                raise ValueError("Workflow session is full; save needed workflows and restart")
            handle = uuid4().hex
            self._recipes[handle] = recipe
            return {
                "workflow_id": handle,
                **recipe.model_dump(mode="json"),
                "prompt": definition.graph,
            }

    def register_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        if self.registry is None:
            raise ValueError("Workflow definitions are not configured")
        if definition.get("id") in {"text-to-image", "text-to-image-lora"}:
            raise ValueError("Built-in workflow template IDs are reserved")
        return self.registry.register(definition)

    def build(
        self,
        template: str,
        parameters: Parameters | dict[str, Any],
        definition_version: int | None = None,
        definition_digest: str | None = None,
        require_ready: bool = False,
    ) -> dict:
        if definition_digest is not None and definition_version is None:
            raise ValueError("Definition digest requires a paired version")
        if type(require_ready) is not bool or (
            definition_version is not None and type(definition_version) is not int
        ):
            raise ValueError("Invalid build preconditions")
        if template in ("text-to-image", "text-to-image-lora"):
            if definition_version is not None or definition_digest is not None:
                raise ValueError("Definition preconditions cannot target a builtin")
            recipe: AnyRecipe = Recipe(
                template=template, parameters=Parameters.model_validate(parameters)
            )
            prompt = build_prompt(recipe, self.catalog)
        else:
            if self.registry is None:
                raise ValueError("Unknown workflow definition ID")
            definition = self.registry.get(template, definition_version)
            if definition_digest is not None and definition.digest != definition_digest:
                raise ValueError("Stale workflow definition digest")
            values = parameters.model_dump() if isinstance(parameters, Parameters) else parameters
            normalized, prompt = self.registry.materialize(
                definition.id, definition.version, values
            )
            recipe = ExternalRecipe(
                template=template,
                definition_version=definition.version,
                definition_digest=definition.digest,
                require_ready=require_ready,
                parameters=normalized,
            )
            self.capture(recipe)
        if len(self._recipes) >= 1024:
            raise ValueError("Workflow session is full; save needed workflows and restart")
        workflow_id = uuid4().hex
        self._recipes[workflow_id] = recipe
        return {"workflow_id": workflow_id, **recipe.model_dump(mode="json"), "prompt": prompt}

    def get(self, workflow_id: str) -> AnyRecipe:
        checked_id(workflow_id)
        if workflow_id in self._recipes:
            return self._recipes[workflow_id]
        path = self.directory / f"{workflow_id}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unknown workflow ID")
        if path.stat().st_size > MAX_RECIPE_BYTES:
            raise ValueError("Saved workflow is too large")
        content = path.read_bytes()
        try:
            from .workflow_v3 import decode_definition

            data = decode_definition(content)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid saved workflow recipe") from exc
        if not isinstance(data, dict):
            raise ValueError("Invalid saved workflow recipe")
        if data.get("schema_version") == 3:
            from .image_v3 import ImageRecipe

            if self.v3 is None:
                raise ValueError("Workflow v3 is disabled")
            return ImageRecipe.model_validate(data)
        if data.get("schema_version", 1) == 2:
            return ExternalRecipe.model_validate(data)
        return Recipe.model_validate(data)

    def capture(self, recipe: AnyRecipe):
        """Recheck identity/policy and capture output identity before provider awaits."""
        if not isinstance(recipe, ExternalRecipe):
            return None
        if recipe.schema_version == 3:
            if self.v3 is None:
                raise ValueError("Workflow v3 is disabled")
            with self.v3.versions.lock:
                return self.v3.capture(recipe)
        if self.registry is None:
            raise ValueError("Workflow definitions are not configured")
        with self.registry._register_lock:
            definition = self.registry.get(recipe.template, recipe.definition_version)
            if (
                recipe.definition_digest is not None
                and recipe.definition_digest != definition.digest
            ):
                raise ValueError("Stale workflow definition digest")
            if recipe.require_ready:
                if self.readiness is None:
                    raise ValueError("Workflow production readiness is unavailable")
                self.readiness.require(definition, recipe.parameters)
            return copy.deepcopy(definition)

    def managed_input_bindings(self, recipe: AnyRecipe) -> dict[str, object]:
        if recipe.schema_version == 3:
            self.capture(recipe)
            return {}
        if not isinstance(recipe, ExternalRecipe):
            return {}
        if self.registry is None:
            raise ValueError("Workflow definitions are not configured")
        return self.registry.managed_input_bindings(recipe.template, recipe.definition_version)

    def prompt(
        self,
        recipe: AnyRecipe,
        job_id: str | None = None,
        provider_inputs: dict[str, str] | None = None,
        definition=None,
    ) -> dict:
        if isinstance(recipe, ExternalRecipe):
            if self.registry is None:
                raise ValueError("Workflow definitions are not configured")
            definition = definition or self.capture(recipe)
            _, prompt = self.registry.materialize_definition(
                definition,
                recipe.parameters,
                job_id,
                provider_inputs,
            )
            return prompt
        prompt = build_prompt(recipe, self.catalog)
        if job_id is not None:
            prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        return prompt

    def routing(self, recipe: AnyRecipe) -> tuple[str, str]:
        if isinstance(recipe, ExternalRecipe):
            definition = self.capture(recipe)
            return definition.provider_id, definition.capability_id
        return "comfyui", "image.generate"

    def save(self, workflow_id: str) -> dict:
        recipe = self.get(workflow_id)
        path = self.directory / f"{workflow_id}.json"
        content = recipe.model_dump_json(indent=2).encode()
        if len(content) > MAX_RECIPE_BYTES:
            raise ValueError("Saved workflow is too large")
        atomic_write(path, content)
        return {"workflow_id": workflow_id, "file": str(path), "saved": True}

    def list(self) -> dict:
        definitions = (
            [
                self.readiness.descriptor(item) if self.readiness else item.metadata()
                for item in self.registry.definitions.values()
            ]
            if self.registry
            else []
        )
        descriptors = builtin_descriptors() + definitions
        if len(descriptors) > 128:
            raise ValueError("Workflow discovery exceeds entry limit")
        saved = []
        for path in sorted(self.directory.glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{32}", path.stem) and not path.is_symlink():
                saved.append(path.stem)
        result = {
            "templates": TEMPLATES,
            "descriptors": descriptors,
            "definitions": definitions,
            "built_workflows": list(self._recipes),
            "saved_workflows": saved,
        }
        # Include legacy aliases and recipe IDs; bound the full public response.
        if len(json.dumps(result, indent=2).encode("utf-8")) > MAX_DISCOVERY_BYTES:
            raise ValueError("Workflow discovery exceeds byte limit")
        return result
