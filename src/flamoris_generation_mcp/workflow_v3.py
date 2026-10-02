"""Strict portable v3 contracts and bounded canonical identities.

This module validates media contracts; it does not authorize or execute them.
Provider artifacts additionally require a configured, reviewed adapter validator.
"""

import hashlib
import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DEFINITION_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


def canonical(value: Any, limit: int = MAX_MANIFEST_BYTES) -> bytes:
    """Bound nesting/types before JSON traversal; booleans are not numbers."""
    pending = [(value, 0)]
    count = 0
    scalar_size = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 32 or count > 100000:
            raise ValueError("JSON structure exceeds limits")
        if type(item) is str:
            scalar_size += len(item)
        elif type(item) is int:
            scalar_size += item.bit_length() // 3 + 1
        if scalar_size > limit:
            raise ValueError("JSON content exceeds byte limit")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("JSON object keys must be strings")
            scalar_size += sum(len(key) for key in item)
            if scalar_size > limit:
                raise ValueError("JSON content exceeds byte limit")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
        elif item is not None and type(item) not in (str, int, bool):
            raise ValueError("Unsupported JSON value")
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("JSON content exceeds byte limit")
    return raw


def digest(domain: str, value: Any) -> str:
    return "sha256:" + hashlib.sha256(domain.encode() + b"\0" + canonical(value)).hexdigest()


def decode_definition(raw: bytes) -> dict:
    if len(raw) > MAX_DEFINITION_BYTES:
        raise ValueError("Definition exceeds byte limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        canonical(value, MAX_DEFINITION_BYTES)
    except (UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("Invalid bounded JSON definition") from exc
    if type(value) is not dict:
        raise ValueError("Definition must be an object")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Profile(Contract):
    id: Identifier
    revision: int = Field(ge=1, le=100000)


class Port(Contract):
    type: Literal["string", "integer", "number", "boolean", "asset", "abc", "json"]
    schema_id: Identifier
    schema_revision: int = Field(ge=1, le=100000)
    role: Identifier
    min_count: int = Field(default=1, ge=0, le=128)
    max_count: int = Field(default=1, ge=1, le=128)
    max_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    media_kind: (
        Literal["image", "audio", "video", "midi", "score", "document", "metadata"] | None
    ) = None
    mime_types: list[str] = Field(default_factory=list, max_length=8)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    default: Any = None

    @model_validator(mode="after")
    def constraints(self):
        import re

        if self.min_count > self.max_count:
            raise ValueError("Invalid port cardinality")
        if self.type != "asset" and (self.min_count > 1 or self.max_count != 1):
            raise ValueError("Scalar/structured ports require single-value cardinality")
        if self.type == "asset":
            if not self.media_kind or not self.mime_types or self.default is not None:
                raise ValueError("Asset ports require media/MIME and no default")
        elif self.media_kind is not None or self.mime_types:
            raise ValueError("Media declarations require asset ports")
        if len(set(self.mime_types)) != len(self.mime_types) or any(
            not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime) for mime in self.mime_types
        ):
            raise ValueError("Invalid MIME declarations")
        if (self.minimum is not None or self.maximum is not None) and self.type not in (
            "number",
            "integer",
        ):
            raise ValueError("Numeric bounds require numeric port")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Invalid numeric bounds")
        if self.default is not None:
            if self.min_count != 0:
                raise ValueError("Only optional input ports may have defaults")
            object.__setattr__(self, "default", self.validate_value(self.default))
        return self

    def validate_value(self, value: Any) -> Any:
        """Content check only: asset ownership/expiry is separately authoritative."""
        if self.type == "asset":
            raise ValueError("Asset values require the managed-input profile validator")
        types = {
            "string": (str,),
            "abc": (str,),
            "integer": (int,),
            "number": (int, float),
            "boolean": (bool,),
            "json": (dict, list),
        }
        if type(value) not in types[self.type]:
            raise ValueError("Invalid port value type")
        if type(value) in (int, float):
            if type(value) is float and not math.isfinite(value):
                raise ValueError("Nonfinite port value")
            if self.minimum is not None and value < self.minimum:
                raise ValueError("Port value below minimum")
            if self.maximum is not None and value > self.maximum:
                raise ValueError("Port value above maximum")
        canonical(value, self.max_bytes)
        # ABC/JSON format semantics still require the registered profile validator.
        if self.type == "number":
            try:
                value = float(value)
            except OverflowError as exc:
                raise ValueError("Number exceeds finite range") from exc
            if not math.isfinite(value):
                raise ValueError("Number exceeds finite range")
        return value


class Budget(Contract):
    steps: int = Field(ge=1, le=256)
    deadline_seconds: int = Field(ge=1, le=86400)
    output_assets: int = Field(ge=1, le=128)
    output_bytes: int = Field(ge=1, le=64 * 1024 * 1024)


class Include(Contract):
    alias: Identifier
    workflow_id: Identifier
    version: int = Field(ge=1, le=100000)
    digest: Digest


class Binding(Contract):
    source: str = Field(alias="from", max_length=160)
    target: str = Field(alias="to", max_length=160)


class ProviderExecution(Contract):
    kind: Literal["provider"]
    provider_id: Identifier
    adapter_revision: int = Field(ge=1, le=100000)
    artifact: dict[str, Any]


class CompositionExecution(Contract):
    kind: Literal["composition"]


class Definition(Contract):
    schema_version: Literal[3]
    id: Identifier
    version: int = Field(ge=1, le=100000)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*$", max_length=129)
    profile: Profile
    inputs: dict[Identifier, Port] = Field(default_factory=dict, max_length=64)
    outputs: dict[Identifier, Port] = Field(min_length=1, max_length=64)
    budget: Budget
    effects: list[Literal["pure", "read", "write", "external", "destructive", "paid"]] = Field(
        min_length=1, max_length=6
    )
    execution: Annotated[ProviderExecution | CompositionExecution, Field(discriminator="kind")]
    includes: list[Include] = Field(default_factory=list, max_length=64)
    bindings: list[Binding] = Field(default_factory=list, max_length=1024)

    @model_validator(mode="after")
    def invariants(self):
        if len(self.inputs) + len(self.outputs) > 64:
            raise ValueError("Public port count exceeds limit")
        if len({p.role for p in self.outputs.values()}) != len(self.outputs):
            raise ValueError("Duplicate output role")
        if any(p.default is not None for p in self.outputs.values()):
            raise ValueError("Output ports cannot declare defaults")
        effects = set(self.effects)
        if len(effects) != len(self.effects) or ("pure" in effects and len(effects) != 1):
            raise ValueError("Contradictory or duplicate effects")
        if "destructive" in effects and "write" not in effects:
            raise ValueError("Destructive effect requires write")
        if isinstance(self.execution, ProviderExecution):
            if self.includes or self.bindings:
                raise ValueError("Provider definitions cannot carry composition bindings")
            if sum(p.max_count for p in self.outputs.values()) > self.budget.output_assets:
                raise ValueError("Declared outputs exceed asset budget")
            if sum(p.max_bytes * p.max_count for p in self.outputs.values()) > (
                self.budget.output_bytes
            ):
                raise ValueError("Declared outputs exceed byte budget")
        elif not self.includes or not self.bindings:
            raise ValueError("Composition requires includes and bindings")
        aliases = [include.alias for include in self.includes]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Duplicate include alias")
        object.__setattr__(self, "effects", sorted(self.effects))
        canonical(self.model_dump(mode="json", by_alias=True), MAX_DEFINITION_BYTES)
        return self

    @property
    def identity(self):
        return {"id": self.id, "version": self.version, "digest": self.digest}

    @property
    def digest(self):
        return digest("flamoris.definition.v3.1", self.model_dump(mode="json", by_alias=True))

    def descriptor(self):
        return {
            **self.identity,
            "descriptor_revision": 3,
            "name": self.name,
            "description": self.description,
            "capability_id": self.capability_id,
            "profile": self.profile.model_dump(),
            "execution_kind": self.execution.kind,
            "inputs": {k: v.model_dump(exclude_none=True) for k, v in self.inputs.items()},
            "outputs": {k: v.model_dump(exclude_none=True) for k, v in self.outputs.items()},
            "readiness": {"state": "validated", "reason": "composition_verification_required"},
        }
