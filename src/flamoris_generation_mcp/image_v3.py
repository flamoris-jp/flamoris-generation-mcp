"""Reviewed txt2img v3 adapter, sharing the existing Image-v1 execution authority.

The first production profile permits nested, renamed pass-through compositions
with exactly one Image leaf. It does not qualify native intermediates, multiple
images, managed assets, arbitrary component profiles or different providers.
"""

import copy
import re
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .composition import COMPILER_REVISION
from .definition_versions import DefinitionVersions
from .image_profile import image_topology
from .provider_lowering import GraphArtifact, GraphLowerer
from .workflow_registry import ParameterSpec, WorkflowDefinition
from .workflow_v3 import Contract, Digest, Identifier, Port, Profile, canonical, digest
from .workflows import ExternalRecipe

PROFILE = {"id": "image-generate-v1", "revision": 1}
BOUNDS = {
    "width": ("integer", 64, 4096),
    "height": ("integer", 64, 4096),
    "seed": ("integer", 0, 2**53 - 1),
    "steps": ("integer", 1, 150),
    "cfg": ("number", 0, 100),
    "denoise": ("number", 0, 1),
}


class Identity(Contract):
    id: Identifier
    version: int = Field(ge=1, le=100000)
    digest: Digest


class ImagePlanIdentity(Contract):
    root: Identity
    closure_digest: Digest
    structural_digest: Digest
    compiler_revision: Literal[COMPILER_REVISION] = COMPILER_REVISION
    adapter_revision: Literal[1] = 1
    profile: Profile


class ImageRecipe(ExternalRecipe):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[3] = 3
    definition_digest: Digest
    structural_digest: Digest
    closure_digest: Digest
    invocation_digest: Digest
    compiler_revision: Literal[COMPILER_REVISION] = COMPILER_REVISION
    adapter_revision: Literal[1] = 1


class ImageDefinition(WorkflowDefinition):
    """Captured validated native graph plus immutable media qualification identity."""

    v3_identity: ImagePlanIdentity
    output_port: Identifier
    output_contract: Port

    @model_validator(mode="after")
    def bound_identity(self):
        if (self.id, self.version) != (self.v3_identity.root.id, self.v3_identity.root.version):
            raise ValueError("Compiled Image identity mismatch")
        if self.v3_identity.profile.model_dump() != PROFILE:
            raise ValueError("Unsupported compiled Image profile")
        validate_output(self.output_contract)
        return self

    @property
    def digest(self):
        return self.v3_identity.root.digest


def validate_output(port):
    if (
        port.type,
        port.schema_id,
        port.schema_revision,
        port.role,
        port.media_kind,
        port.min_count,
        port.max_count,
    ) != ("asset", "image-v1", 1, "primary_image", "image", 1, 1) or not set(port.mime_types) <= {
        "image/png",
        "image/jpeg",
        "image/webp",
    }:
        raise ValueError("Image v3 requires one explicit primary image output")


def input_spec(name, port, binding):
    role = port.role
    if port.schema_id != "image-" + role.replace("_", "-") or port.schema_revision != 1:
        raise ValueError("Unknown Image input schema")
    if port.max_count != 1 or (not port.min_count and port.default is None):
        raise ValueError("Optional Image inputs require explicit defaults")
    if role in BOUNDS:
        kind, low, high = BOUNDS[role]
        if (
            port.type != kind
            or port.minimum is None
            or port.minimum < low
            or port.maximum is None
            or port.maximum > high
        ):
            raise ValueError("Invalid Image numeric domain")
    elif role in {"checkpoint", "positive_prompt", "negative_prompt"}:
        if port.type != "string" or port.max_bytes > 20000:
            raise ValueError("Invalid Image text domain")
        if role in {"checkpoint", "positive_prompt"} and port.min_count != 1:
            raise ValueError("Checkpoint and prompt must be required")
    else:
        raise ValueError("Unsupported Image input role")
    return ParameterSpec(
        type=port.type,
        role=role,
        node=binding.node,
        input=binding.input,
        required=bool(port.min_count),
        default=port.default,
        minimum=port.minimum,
        maximum=port.maximum,
        multiple_of=8 if role in {"width", "height"} else None,
        model_kind="checkpoint" if role == "checkpoint" else None,
        min_length=1 if role in {"positive_prompt", "checkpoint"} else None,
        max_length=port.max_bytes if port.type == "string" else None,
    )


def native_definition(identity, name, description, artifact, ports):
    if set(artifact.input_bindings) != set(ports) or len(artifact.output_bindings) != 1:
        raise ValueError("Image native/public port mapping mismatch")
    output = next(iter(artifact.output_bindings.values()))
    # SaveImage UI images are final published files, never a native graph link.
    if output.index != 0 or artifact.graph[output.node].class_type != "SaveImage":
        raise ValueError("Image output must map the final SaveImage collection")
    specs = {k: input_spec(k, p, artifact.input_bindings[k]) for k, p in ports.items()}
    roles = {p.role for p in specs.values()}
    if ("width" in roles) != ("height" in roles):
        raise ValueError("Editable dimensions require both width and height")
    graph = {k: n.model_dump() for k, n in artifact.graph.items()}
    dimensions = (
        {"mode": "parameters"}
        if "width" in roles
        else {"mode": "fixed", "width": 512, "height": 512}
    )
    if dimensions["mode"] == "fixed":
        # Locate dimensions by audited topology, not node IDs or filenames.
        probe = copy.deepcopy(graph)
        latent = [k for k, n in probe.items() if n["class_type"] == "EmptyLatentImage"]
        if len(latent) != 1:
            raise ValueError("Image v3 requires one bounded latent source")
        dimensions.update({k: probe[latent[0]]["inputs"].get(k) for k in ("width", "height")})
    definition = WorkflowDefinition(
        schema_version=2,
        id=identity["id"],
        version=identity["version"],
        name=name,
        description=description,
        provider_id="comfyui",
        capability_id="image.generate",
        graph=graph,
        parameters=specs,
        output_node=output.node,
        image={"profile": "image-v1", "mode": "txt2img", "dimensions": dimensions},
    )
    topology = image_topology(definition)
    sampler = graph[topology["sampler"]]["inputs"]
    for role in ("seed", "steps", "cfg", "denoise"):
        kind, low, high = BOUNDS[role]
        value = sampler.get(role)
        allowed = (int,) if kind == "integer" else (int, float)
        if type(value) not in allowed or not low <= value <= high:
            raise ValueError("Image artifact exceeds effective numeric bounds")
    for role in ("sampler_name", "scheduler"):
        value = sampler.get(role)
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,80}", value):
            raise ValueError("Image artifact requires bounded native selector constants")
    return definition


class ImageV3:
    def __init__(self, workflows):
        self.workflows = workflows
        self.lowerer = GraphLowerer("comfyui", 1, self.validate_lowered)
        self.versions = DefinitionVersions(
            workflows.directory, self.validate, validate_plan=self.validate_plan
        )

    @staticmethod
    def plan_identity(plan):
        manifest = plan.manifest
        return ImagePlanIdentity(
            root=manifest["root"],
            closure_digest=digest("flamoris.closure.v3.1", manifest["closure"]),
            structural_digest=plan.structural_digest,
            profile=manifest["profile"],
        )

    def validate(self, definition):
        if (
            definition.profile.model_dump() != PROFILE
            or definition.capability_id != "image.generate"
        ):
            raise ValueError("Unsupported production v3 profile/capability")
        if set(definition.effects) != {"external", "write"}:
            raise ValueError("Image v3 requires explicit external/write effects")
        if definition.budget.deadline_seconds != 300 or definition.budget.output_assets != 1:
            raise ValueError("Image v3 requires the current 300-second/one-image ceiling")
        if len(definition.outputs) != 1:
            raise ValueError("Image v3 requires one output")
        validate_output(next(iter(definition.outputs.values())))
        if next(iter(definition.outputs.values())).max_bytes > definition.budget.output_bytes:
            raise ValueError("Image public output exceeds byte budget")
        if len({p.role for p in definition.inputs.values()}) != len(definition.inputs):
            raise ValueError("Duplicate Image input role")
        if definition.execution.kind == "provider":
            if (definition.execution.provider_id, definition.execution.adapter_revision) != (
                "comfyui",
                1,
            ):
                raise ValueError("Unsupported Image v3 provider/adapter")
            artifact = GraphArtifact.model_validate(definition.execution.artifact)
            if set(artifact.output_bindings) != set(definition.outputs):
                raise ValueError("Native output mapping differs from public declarations")
            native_definition(
                definition.identity,
                definition.name,
                definition.description,
                artifact,
                definition.inputs,
            )
            if definition.budget.steps < len(artifact.graph):
                raise ValueError("Image graph exceeds declared step budget")

    @staticmethod
    def validate_plan(plan):
        manifest = plan.manifest
        if len(manifest["steps"]) != 1:
            raise ValueError(
                "Image v3 requires exactly one leaf; more components need reviewed native contracts"
            )
        step = manifest["steps"][0]
        mapping = step["inputs"]
        if set(mapping) != set(step["input_ports"]) or any(
            set(s) != {"input"} for s in mapping.values()
        ):
            raise ValueError("Image compositions require explicit pass-through input bindings")
        names = [s["input"] for s in mapping.values()]
        if len(set(names)) != len(names) or set(names) != set(manifest["inputs"]):
            raise ValueError("Image public inputs must map one-to-one to the leaf")
        for output in manifest["outputs"].values():
            if output["source"] != {"step": step["path"], "output": next(iter(step["outputs"]))}:
                raise ValueError("Invalid Image public output binding")
        source = GraphArtifact.model_validate(step["artifact"])
        renamed = GraphArtifact(
            graph=source.graph,
            input_bindings={s["input"]: source.input_bindings[k] for k, s in mapping.items()},
            output_bindings={
                name: next(iter(source.output_bindings.values())) for name in manifest["outputs"]
            },
        )
        native_definition(
            manifest["root"],
            "compiled-image",
            "",
            renamed,
            {k: Port.model_validate(v) for k, v in manifest["inputs"].items()},
        )

    @staticmethod
    def validate_lowered(artifact, manifest):
        step = manifest["steps"][0]
        source = GraphArtifact.model_validate(step["artifact"])
        rewrite = {node: str(i + 1) for i, node in enumerate(sorted(source.graph))}
        # The lowerer assigns one deterministic occurrence. Preserve the audited
        # semantic mappings while allowing only declared scalar slots to vary.
        bindings = {
            mapping["input"]: {
                "node": rewrite[source.input_bindings[name].node],
                "input": source.input_bindings[name].input,
            }
            for name, mapping in step["inputs"].items()
        }
        graph = GraphArtifact.model_validate(
            {
                "graph": artifact["graph"],
                "input_bindings": bindings,
                "output_bindings": {
                    k: {"node": v[0], "index": v[1]} for k, v in artifact["outputs"].items()
                },
            }
        )
        ports = {k: Port.model_validate(v) for k, v in manifest["inputs"].items()}
        native_definition(manifest["root"], "compiled-image", "", graph, ports)

    def materialize(self, workflow_id, version, expected_digest, inputs):
        plan = self.versions.compile(workflow_id, version, expected_digest)
        bound = plan.bind(inputs)
        lowered = self.lowerer.lower(plan, bound["inputs"])
        manifest, artifact = plan.manifest, lowered.artifact
        step = manifest["steps"][0]
        source = GraphArtifact.model_validate(step["artifact"])
        rewrite = {node: str(i + 1) for i, node in enumerate(sorted(source.graph))}
        bindings = {
            mapping["input"]: {
                "node": rewrite[source.input_bindings[name].node],
                "input": source.input_bindings[name].input,
            }
            for name, mapping in step["inputs"].items()
        }
        graph = GraphArtifact.model_validate(
            {
                "graph": artifact["graph"],
                "input_bindings": bindings,
                "output_bindings": {
                    k: {"node": v[0], "index": v[1]} for k, v in artifact["outputs"].items()
                },
            }
        )
        root = self.versions.get(workflow_id, version, expected_digest)
        ports = {k: Port.model_validate(v) for k, v in manifest["inputs"].items()}
        native = native_definition(root.identity, root.name, root.description, graph, ports)
        # Existing role/type/model/dimension validation remains decisive.
        normalized, _ = self.workflows.registry.materialize_definition(native, bound["inputs"])
        output_name, output = next(iter(manifest["outputs"].items()))
        identity = self.plan_identity(plan)
        captured = ImageDefinition(
            **native.model_dump(),
            v3_identity=identity,
            output_port=output_name,
            output_contract=output["port"],
        )
        recipe = ImageRecipe(
            template=root.id,
            definition_version=root.version,
            definition_digest=root.digest,
            parameters=normalized,
            structural_digest=plan.structural_digest,
            closure_digest=identity.closure_digest,
            invocation_digest=lowered.invocation_digest,
        )
        canonical(captured.model_dump(mode="json"), 256 * 1024)
        return recipe, captured

    def capture(self, recipe):
        current, definition = self.materialize(
            recipe.template, recipe.definition_version, recipe.definition_digest, recipe.parameters
        )
        for key in (
            "structural_digest",
            "closure_digest",
            "invocation_digest",
            "compiler_revision",
            "adapter_revision",
        ):
            if getattr(current, key) != getattr(recipe, key):
                raise ValueError("Stale compiled Image identity")
        if recipe.require_ready:
            if self.workflows.readiness is None:
                raise ValueError("Workflow production readiness is unavailable")
            self.workflows.readiness.require(definition, recipe.parameters)
        return definition
