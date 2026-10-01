"""Trusted, versioned ComfyUI API graphs with explicit public input bindings."""

import copy
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import ModelKind
from .models import ModelCatalog

MAX_DEFINITION_BYTES = 256 * 1024
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
# Free-form strings are only safe on known text inputs. Model selectors are
# checked against the model catalog; other selectors need a definition-owned enum.
# Provider-side file selectors need a managed asset resolver in a later phase.
FREE_TEXT_INPUTS = {("CLIPTextEncode", "text")}
FILE_INPUT = re.compile(r"(?:file|path|filename|image|video|audio|asset)", re.I)
ImageRole = Literal[
    "checkpoint",
    "positive_prompt",
    "negative_prompt",
    "width",
    "height",
    "seed",
    "steps",
    "cfg",
    "sampler",
    "scheduler",
    "denoise",
    "initial_image",
]


class ImageDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["parameters", "fixed"]
    width: int | None = Field(default=None, ge=64, le=4096, multiple_of=8)
    height: int | None = Field(default=None, ge=64, le=4096, multiple_of=8)

    @model_validator(mode="after")
    def dimensions(self):
        fixed = self.mode == "fixed"
        if fixed != (self.width is not None and self.height is not None):
            raise ValueError("Fixed dimensions require both width and height")
        if not fixed and (self.width is not None or self.height is not None):
            raise ValueError("Parameter dimensions cannot declare fixed size")
        return self


class ImageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile: Literal["image-v1"]
    mode: Literal["txt2img", "img2img"]
    dimensions: ImageDimensions
    reference_semantics: Literal["initial_image"] | None = None
    resize_policy: Literal["center-crop-resize"] | None = None

    @model_validator(mode="after")
    def semantics(self):
        if self.mode == "img2img":
            if self.reference_semantics != "initial_image" or self.resize_policy is None:
                raise ValueError("img2img requires initial_image and center-crop-resize")
        elif self.reference_semantics is not None or self.resize_policy is not None:
            raise ValueError("txt2img cannot declare reference semantics")
        return self


class ParameterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["string", "integer", "number", "boolean", "managed_input"]
    node: str
    input: str
    media_types: list[Literal["image/png", "image/jpeg", "image/webp"]] | None = Field(
        default=None, min_length=1, max_length=3
    )
    required: bool = True
    default: str | int | float | bool | None = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = Field(default=None, ge=0, le=20000)
    max_length: int | None = Field(default=None, ge=0, le=20000)
    enum: list[str | int | float | bool] | None = Field(default=None, min_length=1, max_length=64)
    model_kind: ModelKind | None = None
    role: ImageRole | None = None
    multiple_of: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def constraints(self):
        if self.multiple_of is not None and self.type != "integer":
            raise ValueError("multiple_of requires an integer parameter")
        if self.minimum is not None and not math.isfinite(self.minimum):
            raise ValueError("Parameter minimum must be finite")
        if self.maximum is not None and not math.isfinite(self.maximum):
            raise ValueError("Parameter maximum must be finite")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Parameter minimum exceeds maximum")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("Parameter min_length exceeds max_length")
        if (self.min_length is not None or self.max_length is not None) and self.type != "string":
            raise ValueError("Length constraints require a string parameter")
        if (self.minimum is not None or self.maximum is not None) and self.type not in (
            "integer",
            "number",
        ):
            raise ValueError("Numeric constraints require a numeric parameter")
        if self.model_kind is not None and self.type != "string":
            raise ValueError("Model references require a string parameter")
        if self.type == "managed_input":
            if self.media_types is None:
                raise ValueError("Managed input parameters require media_types")
            if self.default is not None or not self.required:
                raise ValueError("Managed input parameters must be required without defaults")
            if any(
                value is not None
                for value in (
                    self.minimum,
                    self.maximum,
                    self.min_length,
                    self.max_length,
                    self.enum,
                    self.model_kind,
                )
            ):
                raise ValueError("Managed input parameters do not accept scalar constraints")
        elif self.media_types is not None:
            raise ValueError("media_types requires a managed_input parameter")
        if not self.required and self.default is None:
            raise ValueError("Optional parameters require a default")
        return self

    def validate_value(self, value: Any, catalog: ModelCatalog) -> Any:
        if self.type == "managed_input":
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
                raise ValueError("Managed input must be an input ID")
            return value
        kinds = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
        if self.type == "number":
            valid = type(value) in (int, float)
        else:
            valid = type(value) is kinds[self.type]
        if not valid:
            raise ValueError(f"Expected {self.type}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise ValueError("Number must be finite")
            if self.minimum is not None and value < self.minimum:
                raise ValueError("Below minimum")
            if self.maximum is not None and value > self.maximum:
                raise ValueError("Above maximum")
            if self.multiple_of is not None and value % self.multiple_of != 0:
                raise ValueError("Value must be a multiple_of the declared integer")
        if isinstance(value, str):
            if len(value) > 20000 or (self.min_length is not None and len(value) < self.min_length):
                raise ValueError("Invalid string length")
            if self.max_length is not None and len(value) > self.max_length:
                raise ValueError("Invalid string length")
        if self.enum is not None and not any(
            type(value) is type(item) and value == item for item in self.enum
        ):
            raise ValueError("Value is outside declared enum")
        if self.model_kind is not None:
            catalog.require(self.model_kind, value)
        return value


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1, 2]
    id: str
    version: int = Field(ge=1, le=100000)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=500)
    provider_id: str
    capability_id: str
    graph: dict[str, dict[str, Any]]
    parameters: dict[str, ParameterSpec] = Field(max_length=64)
    output_node: str
    image: ImageSpec | None = None

    @model_validator(mode="after")
    def validate_graph(self):
        if self.schema_version == 1 and (
            self.image is not None
            or any(
                p.role is not None or p.multiple_of is not None for p in self.parameters.values()
            )
        ):
            raise ValueError("Image metadata and roles require definition schema v2")
        if self.schema_version == 2:
            if self.image is None:
                raise ValueError("Definition schema v2 requires Image metadata")
            roles = [p.role for p in self.parameters.values() if p.role is not None]
            if len(roles) != len(set(roles)):
                raise ValueError("Duplicate Image role")
            required = {"checkpoint", "positive_prompt"}
            if self.image.mode == "img2img":
                required |= {"initial_image", "denoise"}
            elif "initial_image" in roles:
                raise ValueError("txt2img cannot declare an initial_image role")
            if self.image.dimensions.mode == "parameters":
                required |= {"width", "height"}
            elif {"width", "height"} & set(roles):
                raise ValueError("Fixed dimensions cannot advertise size roles")
            if not required <= set(roles):
                raise ValueError("Missing required Image role")
            for spec in self.parameters.values():
                role = spec.role
                expected = (
                    "managed_input"
                    if role == "initial_image"
                    else "integer"
                    if role in {"width", "height", "seed", "steps"}
                    else "number"
                    if role in {"cfg", "denoise"}
                    else "string"
                )
                if role and spec.type != expected:
                    raise ValueError("Image role has incompatible parameter type")
                if role == "checkpoint" and spec.model_kind != "checkpoint":
                    raise ValueError("checkpoint role requires checkpoint model_kind")
                if role in {"width", "height"} and (
                    spec.multiple_of != 8
                    or spec.minimum is None
                    or spec.minimum < 64
                    or spec.maximum is None
                    or spec.maximum > 4096
                ):
                    raise ValueError("Dimension roles require bounded multiples of 8")
                if role == "denoise" and (
                    spec.minimum is None
                    or spec.minimum < 0
                    or spec.maximum is None
                    or spec.maximum > 1
                ):
                    raise ValueError("denoise role requires bounds within 0..1")
        if not IDENTIFIER.fullmatch(self.id) or not 1 <= len(self.graph) <= 128:
            raise ValueError("Invalid workflow ID or graph size")
        if not IDENTIFIER.fullmatch(self.provider_id) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}\.[a-z][a-z0-9_-]{0,63}", self.capability_id
        ):
            raise ValueError("Invalid provider or capability ID")
        if self.output_node not in self.graph:
            raise ValueError("Unknown output node")
        bindings = set()
        for node_id, node in self.graph.items():
            if not re.fullmatch(r"[0-9]{1,8}", node_id):
                raise ValueError("Invalid graph node ID")
            if (
                set(node) - {"class_type", "inputs", "_meta"}
                or not isinstance(node.get("class_type"), str)
                or not isinstance(node.get("inputs"), dict)
            ):
                raise ValueError("Expected ComfyUI API-format graph")
            if len(node["inputs"]) > 64:
                raise ValueError("Too many node inputs")
        output = self.graph[self.output_node]
        # The current ComfyUI adapter can safely scope and retrieve SaveImage
        # outputs. Other media outputs require an explicit adapter extension.
        if (self.provider_id, self.capability_id) != ("comfyui", "image.generate"):
            raise ValueError("Unsupported workflow provider or capability")
        if output["class_type"] != "SaveImage" or not isinstance(
            output["inputs"].get("filename_prefix"), str
        ):
            raise ValueError("Image output must be a SaveImage node with filename_prefix")
        if any(
            node_id != self.output_node and node["class_type"].startswith(("Save", "Preview"))
            for node_id, node in self.graph.items()
        ):
            raise ValueError("Undeclared output node")
        for name, spec in self.parameters.items():
            if not IDENTIFIER.fullmatch(name) or spec.node not in self.graph:
                raise ValueError("Invalid parameter binding")
            inputs = self.graph[spec.node]["inputs"]
            if spec.input not in inputs or isinstance(inputs[spec.input], (list, dict)):
                raise ValueError("Parameter must bind an existing literal node input")
            binding = (spec.node, spec.input)
            if binding in bindings or binding == (self.output_node, "filename_prefix"):
                raise ValueError("Duplicate or reserved parameter binding")
            node_type = self.graph[spec.node]["class_type"]
            file_binding = bool(FILE_INPUT.search(spec.input) or FILE_INPUT.search(node_type))
            if spec.type == "managed_input":
                if (node_type, spec.input) != ("LoadImage", "image"):
                    raise ValueError("Managed image input must bind LoadImage.image")
            elif file_binding and not (
                self.schema_version == 2
                and spec.type == "integer"
                and (node_type, spec.input)
                in {
                    ("EmptyLatentImage", "width"),
                    ("EmptyLatentImage", "height"),
                    ("ImageScale", "width"),
                    ("ImageScale", "height"),
                }
            ):
                raise ValueError("File/asset input requires a managed asset resolver")
            if spec.type == "string" and spec.model_kind is None:
                if spec.enum is None and (node_type, spec.input) not in FREE_TEXT_INPUTS:
                    raise ValueError("Free-form string binding requires an audited text input")
            bindings.add(binding)
            # Check defaults and enum values for declared types without requiring installed models.
            if spec.default is not None:
                spec.validate_value(spec.default, _NoModels())
            if spec.enum is not None:
                for item in spec.enum:
                    spec.validate_value(item, _NoModels())
        if self.schema_version == 2:
            from .image_profile import image_topology

            image_topology(self)
        # Full definitions must have a canonical JSON representation, including constants.
        json.dumps(self.model_dump(mode="json"), allow_nan=False)
        return self

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "provider_id": self.provider_id,
            "capability_id": self.capability_id,
            "kind": "definition",
            "metadata_schema_version": self.schema_version,
            "definition_version": self.version,
            "definition_digest": self.digest,
            "readiness": {
                "state": "validated",
                "definition_version": self.version,
                "definition_digest": self.digest,
                "reason": "verification_required",
            },
            **({"image": self.image.model_dump(exclude_none=True)} if self.image else {}),
            "parameters": {
                name: spec.model_dump(mode="json", exclude_none=True, exclude={"node", "input"})
                for name, spec in self.parameters.items()
            },
        }

    @property
    def digest(self) -> str:
        content = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(content).hexdigest()


class _NoModels:
    def require(self, _kind, _name):
        return None


def _sync_directory(path: Path) -> None:
    # Windows does not provide a portable way to fsync a directory handle.
    # The file itself is synced on every platform before it is published.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class WorkflowRegistry:
    def __init__(self, root: Path, catalog: ModelCatalog):
        self.root = root
        self.catalog = catalog
        self.definitions: dict[str, WorkflowDefinition] = {}
        self._register_lock = threading.RLock()
        if not root.exists():
            return
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Workflow definition root must be a directory")
        resolved = root.resolve()
        for path in sorted(root.glob("*.json")):
            if path.is_symlink() or not path.resolve().is_relative_to(resolved):
                raise ValueError("Unsafe workflow definition path")
            if path.stat().st_size > MAX_DEFINITION_BYTES:
                raise ValueError("Workflow definition exceeds size limit")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                definition = WorkflowDefinition.model_validate(raw)
            except (ValueError, OSError, UnicodeError) as exc:
                raise ValueError(f"Malformed workflow definition: {path.name}") from exc
            if path.name != f"{definition.id}.json" or definition.id in self.definitions:
                raise ValueError("Workflow definition filename/ID mismatch or duplicate")
            self.definitions[definition.id] = definition

    def register(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Validate, durably publish, and immediately activate one trusted definition."""
        try:
            definition = WorkflowDefinition.model_validate(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid workflow definition") from exc
        content = definition.model_dump_json(indent=2).encode("utf-8")
        if len(content) > MAX_DEFINITION_BYTES:
            raise ValueError("Workflow definition exceeds size limit")

        with self._register_lock:
            current = self.definitions.get(definition.id)
            if current is None and len(self.definitions) >= 126:
                raise ValueError("Workflow discovery entry limit exceeded")
            if current is not None and definition.version <= current.version:
                raise ValueError("Workflow definition version must increase")

            self.root.mkdir(parents=True, exist_ok=True)
            if not self.root.is_dir() or self.root.is_symlink():
                raise ValueError("Workflow definition root must be a real directory")
            path = self.root / f"{definition.id}.json"
            if path.is_symlink():
                raise ValueError("Unsafe workflow definition path")
            if path.exists() and not path.is_file():
                raise ValueError("Unsafe workflow definition path")

            # Keep a synced copy of the previous file so a failure after the
            # publication rename can restore both disk and the live registry.
            previous_path = None
            if path.exists():
                previous = path.read_bytes()
                if len(previous) > MAX_DEFINITION_BYTES:
                    raise ValueError("Workflow definition exceeds size limit")
                previous_fd, previous_name = tempfile.mkstemp(
                    dir=self.root, prefix=".definition-previous-", suffix=".tmp"
                )
                previous_path = Path(previous_name)
                try:
                    with os.fdopen(previous_fd, "wb") as stream:
                        stream.write(previous)
                        stream.flush()
                        os.chmod(previous_path, stat.S_IMODE(path.stat().st_mode))
                        os.fsync(stream.fileno())
                except BaseException:
                    previous_path.unlink(missing_ok=True)
                    raise

            try:
                descriptor, temporary = tempfile.mkstemp(
                    dir=self.root, prefix=".definition-", suffix=".tmp"
                )
                temporary_path = Path(temporary)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.chmod(temporary_path, 0o644)
                        os.fsync(stream.fileno())
                    published = False
                    try:
                        os.replace(temporary_path, path)
                        published = True
                        _sync_directory(self.root)
                    except OSError:
                        if published:
                            try:
                                if previous_path is None:
                                    path.unlink()
                                else:
                                    os.replace(previous_path, path)
                                _sync_directory(self.root)
                            except OSError as rollback_error:
                                # A failed rollback is ambiguous: keep the live
                                # registry aligned with the actual disk version.
                                if path.exists() and path.read_bytes() == content:
                                    self.definitions[definition.id] = definition
                                raise RuntimeError(
                                    "Workflow definition publication and rollback failed"
                                ) from rollback_error
                        raise
                finally:
                    temporary_path.unlink(missing_ok=True)
            finally:
                if previous_path is not None:
                    previous_path.unlink(missing_ok=True)

            self.definitions[definition.id] = definition
            return {"registered": True, **definition.metadata()}

    def get(self, definition_id: str, version: int | None = None) -> WorkflowDefinition:
        definition = self.definitions.get(definition_id)
        if definition is None or (version is not None and version != definition.version):
            raise ValueError("Unknown workflow definition ID or version")
        return definition

    def managed_input_bindings(self, definition_id: str, version: int) -> dict[str, ParameterSpec]:
        definition = self.get(definition_id, version)
        return {
            name: spec
            for name, spec in definition.parameters.items()
            if spec.type == "managed_input"
        }

    def materialize(
        self,
        definition_id: str,
        version: int,
        parameters: dict,
        job_id: str | None = None,
        provider_inputs: dict[str, str] | None = None,
    ) -> tuple[dict, dict]:
        definition = self.get(definition_id, version)
        return self.materialize_definition(definition, parameters, job_id, provider_inputs)

    def materialize_definition(
        self,
        definition: WorkflowDefinition,
        parameters: dict,
        job_id: str | None = None,
        provider_inputs: dict[str, str] | None = None,
    ) -> tuple[dict, dict]:
        if not isinstance(parameters, dict) or set(parameters) - set(definition.parameters):
            raise ValueError("Unknown workflow parameter")
        graph = copy.deepcopy(definition.graph)
        normalized = {}
        for name, spec in definition.parameters.items():
            if name in parameters:
                value = parameters[name]
            elif spec.required:
                raise ValueError(f"Missing required workflow parameter: {name}")
            elif spec.default is not None:
                value = spec.default
            else:
                raise ValueError(f"Missing required workflow parameter: {name}")
            try:
                normalized[name] = spec.validate_value(value, self.catalog)
            except ValueError as exc:
                raise ValueError(f"Invalid workflow parameter {name}: {exc}") from exc
            if spec.type == "managed_input":
                if provider_inputs is not None:
                    if name not in provider_inputs:
                        raise ValueError(f"Missing resolved managed input: {name}")
                    graph[spec.node]["inputs"][spec.input] = provider_inputs[name]
            else:
                graph[spec.node]["inputs"][spec.input] = normalized[name]
        expected_inputs = {
            name for name, spec in definition.parameters.items() if spec.type == "managed_input"
        }
        if provider_inputs is not None and set(provider_inputs) != expected_inputs:
            raise ValueError("Resolved managed inputs do not match workflow definition")
        if job_id is not None:
            graph[definition.output_node]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        if definition.schema_version == 2:
            from .image_profile import image_topology

            image_topology(definition, graph)
        return normalized, graph
