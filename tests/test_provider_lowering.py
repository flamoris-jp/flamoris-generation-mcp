import copy

import pytest
from test_composition import leaf, parent, port

from flamoris_generation_mcp.composition import Compiler
from flamoris_generation_mcp.provider_lowering import GraphArtifact, GraphLowerer
from flamoris_generation_mcp.workflow_v3 import Definition


def graph_leaf(**changes):
    raw = leaf()
    raw["execution"]["artifact"] = {
        "graph": {"10": {"class_type": "FixtureUpper", "inputs": {"text": "default"}}},
        "input_bindings": {"text": {"node": "10", "input": "text"}},
        "output_bindings": {"text": {"node": "10", "index": 0}},
    }
    raw.update(changes)
    return raw


def compile_plan(raw, children=()):
    definitions = [Definition.model_validate(r) for r in children]
    registry = {(d.id, d.version, d.digest): d for d in definitions}
    return Compiler(lambda i, v, d: registry[(i, v, d)], lambda _: None).compile(
        Definition.model_validate(raw)
    )


def validate_whole(artifact, manifest):
    assert manifest["compiler_revision"] == 2
    for node in artifact["graph"].values():
        if node["class_type"] != "FixtureUpper" or set(node["inputs"]) != {"text"}:
            raise ValueError("Unreviewed fixture node/input")
        value = node["inputs"]["text"]
        if type(value) is list:
            if value[1] != 0:
                raise ValueError("Unknown native output index")
        elif type(value) is not str or len(value.encode()) > 100:
            raise ValueError("Outside fixture qualified input domain")
    if any(source[1] != 0 for source in artifact["outputs"].values()):
        raise ValueError("Unknown native output index")


def lowerer(validator=validate_whole):
    return GraphLowerer("fixture", 1, validator)


def fixture_value(artifact):
    """Test-only interpretation; no production adapter or runtime qualification."""
    values = {}
    for key, node in artifact["graph"].items():
        value = node["inputs"]["text"]
        values[key] = (values[value[0]] if type(value) is list else value).upper()
    return values[artifact["outputs"]["text"][0]]


def test_repeated_occurrences_lower_with_distinct_nodes_and_explicit_indices():
    child = graph_leaf()
    plan = compile_plan(parent([child, child]), [child])
    built = lowerer().lower(plan, {"text": "hello"})
    artifact = built.artifact
    assert artifact["graph"] == {
        "1": {"class_type": "FixtureUpper", "inputs": {"text": "hello"}},
        "2": {"class_type": "FixtureUpper", "inputs": {"text": ["1", 0]}},
    }
    assert artifact["outputs"] == {"text": ["2", 0]}
    assert artifact["node_provenance"] == {
        "1": {"step": "root/c0", "node": "10"},
        "2": {"step": "root/c1", "node": "10"},
    }
    assert artifact["budget"]["steps"] == 2
    assert fixture_value(artifact) == "HELLO"


def test_lowering_is_deterministic_and_invocation_pins_built_constants():
    child = graph_leaf()
    raw = parent([child, child])
    first = compile_plan(raw, [child])
    raw["includes"].reverse()
    raw["bindings"].reverse()
    second = compile_plan(raw, [child])
    # Definition identity includes array order even when the native graph is equivalent.
    a = lowerer().lower(first, {"text": "a"})
    same = lowerer().lower(first, {"text": "a"})
    b = lowerer().lower(first, {"text": "b"})
    assert a == same
    assert a.artifact == lowerer().lower(second, {"text": "a"}).artifact
    assert a.structural_digest == b.structural_digest == first.structural_digest
    assert a.invocation_digest != b.invocation_digest


def test_validator_and_returned_views_cannot_mutate_built_identity():
    plan = compile_plan(graph_leaf())

    def mutating_validator(artifact, manifest):
        validate_whole(artifact, manifest)
        artifact["graph"]["1"]["inputs"]["text"] = "changed"
        manifest.clear()

    built = lowerer(mutating_validator).lower(plan, {"text": "original"})
    view = built.artifact
    view["graph"].clear()
    assert fixture_value(built.artifact) == "ORIGINAL"
    assert built.structural_digest == plan.structural_digest


@pytest.mark.parametrize("provider,revision", [("other", 1), ("fixture", 2)])
def test_mixed_provider_or_revision_requires_bridge(provider, revision):
    child = graph_leaf()
    other = copy.deepcopy(child)
    other["id"] = "other"
    other["execution"].update(provider_id=provider, adapter_revision=revision)
    plan = compile_plan(parent([child, other]), [child, other])
    called = []
    with pytest.raises(ValueError, match="Runtime bridge"):
        lowerer(lambda *args: called.append(args)).lower(plan, {"text": "hello"})
    assert not called


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda a: a["input_bindings"]["text"].update(node="11"), "Unknown native input"),
        (lambda a: a["output_bindings"]["text"].update(node="11"), "Unknown native output"),
        (lambda a: a["graph"]["10"]["inputs"].update(text=["10", 0]), "override"),
        (lambda a: a["graph"]["10"]["inputs"].update(text=["11", 0]), "output reference"),
        (lambda a: a["graph"]["10"]["inputs"].update(text=["10", True]), "output reference"),
        (lambda a: a["graph"].update({"20": {"class_type": "Hidden", "inputs": {}}}), "hides"),
        (
            lambda a: a["input_bindings"].update(other={"node": "10", "input": "text"}),
            "Overlapping",
        ),
        (lambda a: a.update(input_bindings={}), "mapping mismatch"),
        (lambda a: a["graph"]["10"].update(class_type="Unreviewed"), "Unreviewed"),
        (lambda a: a["output_bindings"]["text"].update(index=1), "output index"),
        (lambda a: a["graph"]["10"]["inputs"].update(text={"executable": "data"}), "literal"),
    ],
)
def test_invalid_or_unreviewed_graph_translation_is_rejected(change, reason):
    raw = graph_leaf()
    change(raw["execution"]["artifact"])
    plan = compile_plan(raw)
    with pytest.raises(ValueError, match=reason):
        lowerer().lower(plan, {"text": "hello"})


def test_native_graph_internal_cycle_is_rejected():
    artifact = graph_leaf()["execution"]["artifact"]
    artifact["graph"] = {
        "10": {"class_type": "FixtureUpper", "inputs": {"text": "default", "link": ["20", 0]}},
        "20": {"class_type": "FixtureUpper", "inputs": {"text": ["10", 0]}},
    }
    with pytest.raises(ValueError, match="cycle"):
        GraphArtifact.model_validate(artifact)


def test_aggregate_native_graph_limit_rejects_before_validator():
    child = graph_leaf()
    artifact = child["execution"]["artifact"]
    artifact["graph"] = {
        str(i): {
            "class_type": "FixtureUpper",
            "inputs": {"text": "default" if i == 1 else [str(i - 1), 0]},
        }
        for i in range(1, 66)
    }
    artifact["input_bindings"]["text"]["node"] = "1"
    artifact["output_bindings"]["text"]["node"] = "65"
    plan = compile_plan(parent([child, child]), [child])
    with pytest.raises(ValueError, match="node limit"):
        lowerer().lower(plan, {"text": "hello"})


def test_defaults_are_normalized_but_absent_explicit_binding_never_falls_back():
    child = graph_leaf(inputs={"text": port(min_count=0, default="declared")})
    root = parent([child], inputs={})
    root["bindings"] = [{"from": "c0.outputs.text", "to": "outputs.text"}]
    assert fixture_value(lowerer().lower(compile_plan(root, [child]), {}).artifact) == "DECLARED"
    root = parent([child], inputs={"text": port(min_count=0)})
    with pytest.raises(ValueError, match="Absent explicit"):
        lowerer().lower(compile_plan(root, [child]), {})


def test_structured_value_cannot_masquerade_as_native_connection():
    raw = graph_leaf(inputs={"text": port(type="json")})
    with pytest.raises(ValueError, match="Structured/managed"):
        lowerer().lower(compile_plan(raw), {"text": ["1", 0]})


def test_output_only_nodes_unused_by_root_are_rejected_in_whole_graph():
    child = graph_leaf(
        outputs={"text": port(), "other": port(role="other")},
        budget={"steps": 1, "deadline_seconds": 1, "output_assets": 2, "output_bytes": 200},
    )
    artifact = child["execution"]["artifact"]
    artifact["graph"]["20"] = {"class_type": "FixtureUpper", "inputs": {"text": "unused"}}
    artifact["output_bindings"]["other"] = {"node": "20", "index": 0}
    plan = compile_plan(parent([child]), [child])
    with pytest.raises(ValueError, match="Disconnected"):
        lowerer().lower(plan, {"text": "hello"})


def test_required_whole_validator_rechecks_concrete_domain():
    plan = compile_plan(graph_leaf())
    with pytest.raises(ValueError, match="validator"):
        GraphLowerer("fixture", 1, None)

    def domain(artifact, manifest):
        validate_whole(artifact, manifest)
        if artifact["graph"]["1"]["inputs"]["text"] != "smoke":
            raise ValueError("Outside measured domain")

    lowerer(domain).lower(plan, {"text": "smoke"})
    with pytest.raises(ValueError, match="measured domain"):
        lowerer(domain).lower(plan, {"text": "production"})
