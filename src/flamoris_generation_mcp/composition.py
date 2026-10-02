"""Finite include resolution and typed binding compiler, with no execution side effects."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from .workflow_v3 import Definition, Port, ProviderExecution, canonical, digest

COMPILER_REVISION = 1
MAX_OCCURRENCES = 64
MAX_DEPTH = 8
MAX_EDGES = 1024


def compatible(source: Port, target: Port):
    if (source.type, source.schema_id, source.schema_revision, source.media_kind, source.role) != (
        target.type,
        target.schema_id,
        target.schema_revision,
        target.media_kind,
        target.role,
    ):
        raise ValueError("Incompatible public port type/schema/media")
    if source.min_count < target.min_count or source.max_count > target.max_count:
        raise ValueError("Incompatible port cardinality")
    if source.max_bytes > target.max_bytes:
        raise ValueError("Source exceeds target byte bound")
    if not set(source.mime_types) <= set(target.mime_types):
        raise ValueError("Source MIME domain exceeds target domain")
    for attr, unsafe in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b)):
        left, right = getattr(source, attr), getattr(target, attr)
        if right is not None and (left is None or unsafe(left, right)):
            raise ValueError("Source numeric domain exceeds target domain")


@dataclass(frozen=True)
class Plan:
    manifest: dict

    @property
    def structural_digest(self):
        return digest("flamoris.media-plan.v3.1", self.manifest)

    def bind(self, inputs: dict):
        """Normalize only declared scalar slots; media needs a qualified adapter."""
        ports = {name: Port.model_validate(raw) for name, raw in self.manifest["inputs"].items()}
        if type(inputs) is not dict or set(inputs) - set(ports):
            raise ValueError("Unknown invocation input")
        normalized = {}
        for name, port in ports.items():
            if name in inputs:
                normalized[name] = port.validate_value(inputs[name])
            elif port.default is not None:
                normalized[name] = port.validate_value(port.default)
            elif port.min_count:
                raise ValueError("Missing required invocation input")
        invocation = {"structural_digest": self.structural_digest, "inputs": normalized}
        return {**invocation, "invocation_digest": digest("flamoris.invocation.v3.1", invocation)}


class Compiler:
    def __init__(
        self,
        lookup: Callable[[str, int, str], Definition],
        validate: Callable[[Definition], None],
    ):
        self.lookup = lookup
        self.validate = validate

    def compile(self, root: Definition) -> Plan:
        state = {"occurrences": 0, "edges": 0, "bytes": 0, "steps": [], "closure": {}}
        inputs = {name: {"input": name} for name in root.inputs}
        outputs, budget, effects = self._expand(root, "root", inputs, (), state)
        manifest = {
            "manifest_revision": 1,
            "compiler_revision": COMPILER_REVISION,
            "root": root.identity,
            "profile": root.profile.model_dump(),
            "inputs": {k: v.model_dump(mode="json") for k, v in root.inputs.items()},
            "outputs": {
                k: {"port": v.model_dump(mode="json"), "source": outputs[k]}
                for k, v in root.outputs.items()
            },
            "closure": sorted(state["closure"].values(), key=lambda x: (x["id"], x["version"])),
            "steps": state["steps"],
            "budget": budget,
            "effects": sorted(effects),
        }
        canonical(manifest)
        return Plan(manifest)

    def _expand(self, definition, path, assigned, ancestors, state):
        key = (definition.id, definition.version, definition.digest)
        if key in ancestors:
            raise ValueError("Include cycle")
        if len(ancestors) > MAX_DEPTH:
            raise ValueError("Include depth exceeds limit")
        self.validate(definition)
        state["bytes"] += len(canonical(definition.model_dump(mode="json", by_alias=True)))
        if state["bytes"] > 2 * 1024 * 1024:
            raise ValueError("Expanded closure exceeds byte limit")
        state["closure"][key] = definition.identity
        if isinstance(definition.execution, ProviderExecution):
            state["steps"].append(
                {
                    "path": path,
                    "definition": definition.identity,
                    "provider_id": definition.execution.provider_id,
                    "adapter_revision": definition.execution.adapter_revision,
                    "profile": definition.profile.model_dump(),
                    "inputs": assigned,
                    "artifact": definition.execution.artifact,
                    "outputs": {
                        k: v.model_dump(mode="json") for k, v in definition.outputs.items()
                    },
                    "effects": sorted(definition.effects),
                    "budget": definition.budget.model_dump(),
                }
            )
            return (
                {name: {"step": path, "output": name} for name in definition.outputs},
                definition.budget.model_dump(),
                set(definition.effects),
            )

        children = {}
        for include in definition.includes:
            state["occurrences"] += 1
            if state["occurrences"] > MAX_OCCURRENCES:
                raise ValueError("Include occurrence count exceeds limit")
            child = self.lookup(include.workflow_id, include.version, include.digest)
            if child.identity != {
                "id": include.workflow_id,
                "version": include.version,
                "digest": include.digest,
            }:
                raise ValueError("Include lookup returned a different identity")
            children[include.alias] = child

        # Each destination has one source; dependency order is separate from include order.
        incoming, dependencies = {}, {alias: set() for alias in children}
        for binding in definition.bindings:
            state["edges"] += 1
            if state["edges"] > MAX_EDGES:
                raise ValueError("Expanded binding count exceeds limit")
            src_owner, src_name, src_port = self._endpoint(
                binding.source, True, definition, children
            )
            dst_owner, dst_name, dst_port = self._endpoint(
                binding.target, False, definition, children
            )
            compatible(src_port, dst_port)
            target = (dst_owner, dst_name)
            if target in incoming:
                raise ValueError("Multiple sources for public input/output")
            incoming[target] = (src_owner, src_name)
            if src_owner is not None and dst_owner is not None:
                dependencies[dst_owner].add(src_owner)

        for alias, child in children.items():
            for name, port in child.inputs.items():
                if (alias, name) not in incoming and port.min_count:
                    raise ValueError("Missing required child input binding")
        if any((None, name) not in incoming for name in definition.outputs):
            raise ValueError("Missing public output binding")

        # No disconnected component can hide paid/destructive effects or unused executions.
        reachable = {src[0] for dst, src in incoming.items() if dst[0] is None}
        pending = list(reachable)
        while pending:
            alias = pending.pop()
            if alias is None:
                continue
            for predecessor in dependencies[alias] - reachable:
                reachable.add(predecessor)
                pending.append(predecessor)
        if set(children) - reachable:
            raise ValueError("Disconnected include hides execution/effects")

        order, remaining = [], dict(dependencies)
        while remaining:
            ready = sorted(alias for alias, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError("Binding dependency cycle")
            order.extend(ready)
            for alias in ready:
                del remaining[alias]
            for deps in remaining.values():
                deps.difference_update(ready)

        results, totals, effects = {}, dict.fromkeys(definition.budget.model_dump(), 0), set()

        def resolve(source):
            owner, name = source
            if owner is None:
                if name not in assigned:
                    raise ValueError("Optional absent input cannot satisfy a binding")
                return assigned[name]
            return results[owner][name]

        for alias in order:
            child = children[alias]
            values = {}
            for name, port in child.inputs.items():
                source = incoming.get((alias, name))
                if source is not None:
                    values[name] = resolve(source)
                elif port.default is not None:
                    values[name] = {"constant": port.validate_value(port.default)}
            result, budget, child_effects = self._expand(
                child, path + "/" + alias, values, (*ancestors, key), state
            )
            results[alias] = result
            effects |= child_effects
            for name, amount in budget.items():
                totals[name] += amount
                if totals[name] > getattr(definition.budget, name):
                    raise ValueError("Expanded plan exceeds aggregate budget")
        if len(effects) > 1:
            effects.discard("pure")
        if effects != set(definition.effects):
            raise ValueError("Composition effects do not match expanded effects")
        return (
            {name: resolve(incoming[(None, name)]) for name in definition.outputs},
            totals,
            effects,
        )

    @staticmethod
    def _endpoint(value, source, root, children):
        match = re.fullmatch(
            r"(?:(?P<alias>[a-z][a-z0-9_-]{0,63})\.)?"
            r"(?P<direction>inputs|outputs)\."
            r"(?P<port>[a-z][a-z0-9_-]{0,63})",
            value,
        )
        if match is None:
            raise ValueError("Invalid public port reference")
        alias, direction, name = match.group("alias", "direction", "port")
        expected = (
            "outputs"
            if source and alias
            else "inputs"
            if source
            else ("inputs" if alias else "outputs")
        )
        if direction != expected or (alias is not None and alias not in children):
            raise ValueError("Invalid binding direction or alias")
        definition = children[alias] if alias else root
        ports = definition.inputs if direction == "inputs" else definition.outputs
        if name not in ports:
            raise ValueError("Unknown public port")
        return alias, name, ports[name]
