"""Trusted, versioned ComfyUI API graphs with explicit public input bindings."""

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import ModelKind
from .models import ModelCatalog

MAX_DEFINITION_BYTES = 256 * 1024
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class ParameterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["string", "integer", "number", "boolean"]
    node: str
    input: str
    required: bool = True
    default: str | int | float | bool | None = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = Field(default=None, ge=0, le=20000)
    max_length: int | None = Field(default=None, ge=0, le=20000)
    enum: list[str | int | float | bool] | None = Field(default=None, min_length=1, max_length=64)
    model_kind: ModelKind | None = None

    @model_validator(mode="after")
    def constraints(self):
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
        if not self.required and self.default is None:
            raise ValueError("Optional parameters require a default")
        return self

    def validate_value(self, value: Any, catalog: ModelCatalog) -> Any:
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

    schema_version: Literal[1]
    id: str
    version: int = Field(ge=1, le=100000)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=500)
    provider_id: Literal["comfyui"]
    capability_id: Literal["image.generate"]
    graph: dict[str, dict[str, Any]]
    parameters: dict[str, ParameterSpec] = Field(max_length=64)
    output_node: str

    @model_validator(mode="after")
    def validate_graph(self):
        if not IDENTIFIER.fullmatch(self.id) or not 1 <= len(self.graph) <= 128:
            raise ValueError("Invalid workflow ID or graph size")
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
        if output["class_type"] != "SaveImage" or not isinstance(
            output["inputs"].get("filename_prefix"), str
        ):
            raise ValueError("Image output must be a SaveImage node with filename_prefix")
        for name, spec in self.parameters.items():
            if not IDENTIFIER.fullmatch(name) or spec.node not in self.graph:
                raise ValueError("Invalid parameter binding")
            inputs = self.graph[spec.node]["inputs"]
            if spec.input not in inputs or isinstance(inputs[spec.input], (list, dict)):
                raise ValueError("Parameter must bind an existing literal node input")
            binding = (spec.node, spec.input)
            if binding in bindings or binding == (self.output_node, "filename_prefix"):
                raise ValueError("Duplicate or reserved parameter binding")
            bindings.add(binding)
            # Check defaults and enum values for declared types without requiring installed models.
            if spec.default is not None:
                spec.validate_value(spec.default, _NoModels())
            if spec.enum is not None:
                for item in spec.enum:
                    spec.validate_value(item, _NoModels())
        return self

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "provider_id": self.provider_id,
            "capability_id": self.capability_id,
            "parameters": {
                name: spec.model_dump(mode="json", exclude_none=True, exclude={"node", "input"})
                for name, spec in self.parameters.items()
            },
        }


class _NoModels:
    def require(self, _kind, _name):
        return None


class WorkflowRegistry:
    def __init__(self, root: Path, catalog: ModelCatalog):
        self.catalog = catalog
        self.definitions: dict[str, WorkflowDefinition] = {}
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

    def get(self, definition_id: str, version: int | None = None) -> WorkflowDefinition:
        definition = self.definitions.get(definition_id)
        if definition is None or (version is not None and version != definition.version):
            raise ValueError("Unknown workflow definition ID or version")
        return definition

    def materialize(
        self, definition_id: str, version: int, parameters: dict, job_id: str | None = None
    ) -> tuple[dict, dict]:
        definition = self.get(definition_id, version)
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
            graph[spec.node]["inputs"][spec.input] = normalized[name]
        if job_id is not None:
            graph[definition.output_node]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        return normalized, graph
