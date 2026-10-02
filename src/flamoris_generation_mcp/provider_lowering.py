"""Deterministic one-provider native graph lowering, without execution authority.

Only a configured adapter may approve node semantics, native output indices,
model/input domains and the entire expanded artifact. No adapter is activated by
this module. In particular, this does not widen the current Image profile.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import Field, model_validator

from .composition import Plan
from .workflow_v3 import Contract, Identifier, Port, canonical, digest

LOWERING_REVISION = 1
MAX_NODES = 128  # Preserve the current trusted graph limit, even across includes.


class InputBinding(Contract):
    node: str = Field(pattern=r"^[0-9]{1,8}$")
    input: Identifier


class OutputBinding(Contract):
    node: str = Field(pattern=r"^[0-9]{1,8}$")
    index: int = Field(ge=0, le=127)


class Node(Contract):
    class_type: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_]+$")
    inputs: dict[Identifier, Any] = Field(max_length=64)


class GraphArtifact(Contract):
    graph: dict[str, Node] = Field(min_length=1, max_length=MAX_NODES)
    input_bindings: dict[Identifier, InputBinding] = Field(max_length=64)
    output_bindings: dict[Identifier, OutputBinding] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def graph_contract(self):
        dependencies = {}
        for key, node in self.graph.items():
            if not re.fullmatch(r"[0-9]{1,8}", key):
                raise ValueError("Invalid native graph node ID")
            dependencies[key] = set()
            for value in node.inputs.values():
                if type(value) is list:
                    if (
                        len(value) != 2
                        or type(value[0]) is not str
                        or value[0] not in self.graph
                        or type(value[1]) is not int
                        or not 0 <= value[1] <= 127
                    ):
                        raise ValueError("Invalid native graph output reference")
                    dependencies[key].add(value[0])
                elif value is not None and type(value) not in (str, int, float, bool):
                    raise ValueError("Unsupported native graph literal")
        targets = set()
        for binding in self.input_bindings.values():
            key = (binding.node, binding.input)
            if key in targets:
                raise ValueError("Overlapping native input bindings")
            if (
                binding.node not in self.graph
                or binding.input not in self.graph[binding.node].inputs
            ):
                raise ValueError("Unknown native input binding")
            if type(self.graph[binding.node].inputs[binding.input]) is list:
                raise ValueError("Public input cannot override an internal reference")
            targets.add(key)
        if any(binding.node not in self.graph for binding in self.output_bindings.values()):
            raise ValueError("Unknown native output binding")
        pending = {binding.node for binding in self.output_bindings.values()}
        reachable = set()
        while pending:
            key = pending.pop()
            if key not in reachable:
                reachable.add(key)
                pending.update(dependencies[key] - reachable)
        if set(self.graph) - reachable:
            raise ValueError("Disconnected native graph hides execution")
        while dependencies:
            ready = {key for key, deps in dependencies.items() if not deps}
            if not ready:
                raise ValueError("Native graph cycle")
            dependencies = {
                key: deps - ready for key, deps in dependencies.items() if key not in ready
            }
        canonical(self.model_dump(mode="json"))
        return self


@dataclass(frozen=True)
class LoweredArtifact:
    """Immutable bytes prevent later caller/validator mutation of built identity."""

    _content: bytes
    structural_digest: str
    invocation_digest: str

    @property
    def artifact(self) -> dict:
        return json.loads(self._content)


@dataclass(frozen=True)
class NativeReference:
    node: str
    index: int

    def as_list(self):
        return [self.node, self.index]


class GraphLowerer:
    def __init__(
        self,
        provider_id: str,
        adapter_revision: int,
        validate: Callable[[dict, dict], None],
    ):
        """validate is a trusted whole-artifact/domain validator, not tool input."""
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_id):
            raise ValueError("Invalid lowering provider")
        if type(adapter_revision) is not int or not 1 <= adapter_revision <= 100000:
            raise ValueError("Invalid lowering adapter revision")
        if not callable(validate):
            raise ValueError("Whole-artifact validator is required")
        self.provider_id, self.adapter_revision, self.validate = (
            provider_id,
            adapter_revision,
            validate,
        )

    def lower(self, plan: Plan, inputs: dict) -> LoweredArtifact:
        # Snapshot before any trusted callback. A built plan is not authorization;
        # the registry/dispatcher must still check its exact pins and evidence.
        manifest = json.loads(canonical(plan.manifest))
        captured = Plan(manifest)
        bound = captured.bind(inputs)
        steps = manifest["steps"]
        if not steps or any(
            (step["provider_id"], step["adapter_revision"])
            != (self.provider_id, self.adapter_revision)
            for step in steps
        ):
            raise ValueError("Mixed or unsupported provider/adapter requires Runtime bridge")
        graph, outputs, provenance = {}, {}, {}
        for step in steps:
            path = step["path"]
            if path in outputs:
                raise ValueError("Duplicate native graph occurrence")
            artifact = GraphArtifact.model_validate(step["artifact"])
            if set(artifact.input_bindings) != set(step["input_ports"]) or set(
                artifact.output_bindings
            ) != set(step["outputs"]):
                raise ValueError("Native graph/public port mapping mismatch")
            if set(step["inputs"]) - set(step["input_ports"]):
                raise ValueError("Unknown compiled native input")
            if len(graph) + len(artifact.graph) > MAX_NODES:
                raise ValueError("Expanded native graph exceeds node limit")
            rewriting = {
                key: str(len(graph) + index + 1) for index, key in enumerate(sorted(artifact.graph))
            }
            for key, node in artifact.graph.items():
                graph[rewriting[key]] = {
                    "class_type": node.class_type,
                    "inputs": {
                        name: [rewriting[value[0]], value[1]] if type(value) is list else value
                        for name, value in node.inputs.items()
                    },
                }
                provenance[rewriting[key]] = {"step": path, "node": key}
            for name, binding in artifact.input_bindings.items():
                source = step["inputs"].get(name)
                port = Port.model_validate(step["input_ports"][name])
                if source is None:
                    # A declared omitted input keeps its definition-owned native
                    # constant. No absent explicit binding gets a hidden default.
                    if port.min_count or port.type == "asset":
                        raise ValueError("Missing required native graph input")
                    port.validate_value(artifact.graph[binding.node].inputs[binding.input])
                    continue
                value = self._source(source, bound["inputs"], outputs)
                if isinstance(value, NativeReference):
                    value = value.as_list()
                else:
                    value = port.validate_value(value)
                graph[rewriting[binding.node]]["inputs"][binding.input] = value
            outputs[path] = {
                name: NativeReference(rewriting[binding.node], binding.index)
                for name, binding in artifact.output_bindings.items()
            }
        public = {
            name: self._source(output["source"], bound["inputs"], outputs)
            for name, output in manifest["outputs"].items()
        }
        if any(not isinstance(source, NativeReference) for source in public.values()):
            raise ValueError("Native graph public output must reference an explicit node/index")
        public = {name: source.as_list() for name, source in public.items()}
        result = {
            "lowering_revision": LOWERING_REVISION,
            "provider_id": self.provider_id,
            "adapter_revision": self.adapter_revision,
            "graph": graph,
            "outputs": public,
            "node_provenance": provenance,
            "budget": manifest["budget"],
        }
        # Revalidate the expanded graph, not merely separately valid components.
        GraphArtifact.model_validate(
            {
                "graph": graph,
                "input_bindings": {},
                "output_bindings": {
                    name: {"node": source[0], "index": source[1]} for name, source in public.items()
                },
            }
        )
        raw = canonical(result)
        self.validate(json.loads(raw), json.loads(canonical(manifest)))
        invocation = {
            "media_invocation_digest": bound["invocation_digest"],
            "artifact_digest": digest("flamoris.native-artifact.v3.1", result),
            "lowering_revision": LOWERING_REVISION,
        }
        return LoweredArtifact(
            raw, captured.structural_digest, digest("flamoris.lowered-invocation.v3.1", invocation)
        )

    @staticmethod
    def _source(source, inputs, outputs):
        if set(source) == {"input"}:
            if source["input"] not in inputs:
                raise ValueError("Absent explicit input binding")
            value = inputs[source["input"]]
            if value is None or type(value) not in (str, int, float, bool):
                raise ValueError("Structured/managed input requires a reviewed staging translation")
            return value
        if set(source) == {"constant"}:
            value = source["constant"]
            if value is None or type(value) not in (str, int, float, bool):
                raise ValueError("Structured constant requires a reviewed translation")
            return value
        if set(source) == {"step", "output"}:
            try:
                return outputs[source["step"]][source["output"]]
            except KeyError:
                raise ValueError("Unknown or forward native output reference") from None
        raise ValueError("Invalid compiled public source")
