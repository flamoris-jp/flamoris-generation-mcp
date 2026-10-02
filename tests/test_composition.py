import copy

import pytest

from flamoris_generation_mcp.composition import Compiler, compatible
from flamoris_generation_mcp.definition_versions import DefinitionVersions
from flamoris_generation_mcp.durable import CommitUnknown
from flamoris_generation_mcp.workflow_v3 import Definition, Port, canonical, decode_definition


def port(**kwargs):
    return {
        "type": "string",
        "schema_id": "bounded-text",
        "schema_revision": 1,
        "role": "text",
        "max_bytes": 100,
        **kwargs,
    }


def leaf(name="child", version=1, **kwargs):
    raw = {
        "schema_version": 3,
        "id": name,
        "version": version,
        "name": name,
        "capability_id": "example.transform",
        "profile": {"id": "fixture-v1", "revision": 1},
        "inputs": {"text": port()},
        "outputs": {"text": port()},
        "budget": {"steps": 1, "deadline_seconds": 1, "output_assets": 1, "output_bytes": 100},
        "effects": ["pure"],
        "execution": {
            "kind": "provider",
            "provider_id": "fixture",
            "adapter_revision": 1,
            "artifact": {"operation": "uppercase"},
        },
    }
    raw.update(kwargs)
    return raw


def include(raw, alias):
    definition = Definition.model_validate(raw)
    return {
        "alias": alias,
        "workflow_id": definition.id,
        "version": definition.version,
        "digest": definition.digest,
    }


def parent(children, name="parent", **kwargs):
    raw = leaf(name)
    raw["execution"] = {"kind": "composition"}
    raw["includes"] = [include(child, f"c{i}") for i, child in enumerate(children)]
    raw["bindings"] = [{"from": "inputs.text", "to": "c0.inputs.text"}]
    for i in range(len(children) - 1):
        raw["bindings"].append({"from": f"c{i}.outputs.text", "to": f"c{i + 1}.inputs.text"})
    raw["bindings"].append({"from": f"c{len(children) - 1}.outputs.text", "to": "outputs.text"})
    raw["budget"] = {
        "steps": 256,
        "deadline_seconds": 86400,
        "output_assets": 128,
        "output_bytes": 64 * 1024 * 1024,
    }
    raw.update(kwargs)
    return raw


def validate_fixture(definition):
    if definition.profile.id != "fixture-v1" or definition.profile.revision != 1:
        raise ValueError("Unqualified profile")
    if definition.execution.kind == "provider":
        if (
            definition.execution.provider_id != "fixture"
            or definition.execution.adapter_revision != 1
            or definition.execution.artifact != {"operation": "uppercase"}
        ):
            raise ValueError("Unsupported fixture artifact")


def store(tmp_path):
    return DefinitionVersions(tmp_path, validate_fixture)


def test_pinned_child_survives_active_update_restart_and_caller_mutation(tmp_path):
    versions = store(tmp_path)
    child = leaf()
    versions.register(child)
    root = parent([child])
    result = versions.register(root)
    root_digest = result["digest"]
    plan = versions.compile("parent", 1, root_digest)
    child["execution"]["artifact"]["operation"] = "evil"
    root["includes"][0]["digest"] = "sha256:" + "a" * 64
    versions.register(leaf(version=2))
    restarted = store(tmp_path)
    assert restarted.get("child").version == 2
    assert restarted.get("child", 1).version == 1
    assert restarted.compile("parent", 1, root_digest).structural_digest == plan.structural_digest
    captured = restarted.get("child", 1)
    captured.execution.artifact["operation"] = "evil"
    assert restarted.get("child", 1).execution.artifact == {"operation": "uppercase"}


def test_identical_publication_is_idempotent_different_content_is_rejected(tmp_path):
    versions = store(tmp_path)
    versions.register(leaf())
    assert versions.register(leaf())["unchanged"]
    with pytest.raises(ValueError, match="Immutable"):
        versions.register(leaf(name="child", description="changed"))


def test_revocation_invalidates_parent_but_active_alias_update_does_not(tmp_path):
    versions = store(tmp_path)
    result = versions.register(leaf())
    root = versions.register(parent([leaf()]))
    versions.revoke("child", 1, result["digest"])
    restarted = store(tmp_path)
    with pytest.raises(ValueError, match="revoked"):
        restarted.compile("parent", 1, root["digest"])


def test_structural_identity_is_distinct_from_bound_invocation(tmp_path):
    versions = store(tmp_path)
    result = versions.register(leaf())
    plan = versions.compile("child", 1, result["digest"])
    a, b = plan.bind({"text": "one"}), plan.bind({"text": "two"})
    assert a["structural_digest"] == b["structural_digest"]
    assert a["invocation_digest"] != b["invocation_digest"]
    assert "readiness" not in plan.manifest
    with pytest.raises(ValueError, match="Unknown"):
        plan.bind({"text": "one", "url": "https://example.com"})
    with pytest.raises(ValueError, match="Missing"):
        plan.bind({})


def test_repeated_child_occurrences_are_not_collapsed(tmp_path):
    versions = store(tmp_path)
    versions.register(leaf())
    root = versions.register(parent([leaf(), leaf()]))
    plan = versions.compile("parent", 1, root["digest"])
    assert len(plan.manifest["steps"]) == 2
    assert len(plan.manifest["closure"]) == 2  # one immutable child plus root
    assert plan.manifest["steps"][1]["inputs"]["text"] == {"step": "root/c0", "output": "text"}


def test_binding_order_is_topologically_resolved(tmp_path):
    versions = store(tmp_path)
    versions.register(leaf())
    root = parent([leaf(), leaf()])
    root["includes"].reverse()
    result = versions.register(root)
    plan = versions.compile("parent", 1, result["digest"])
    assert [s["path"] for s in plan.manifest["steps"]] == ["root/c0", "root/c1"]


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda r: r["includes"][0].update(digest="sha256:" + "f" * 64), "digest"),
        (lambda r: r["bindings"][0].update(to="c0.internal.text"), "reference"),
        (lambda r: r["bindings"].append(copy.deepcopy(r["bindings"][0])), "Multiple"),
        (lambda r: r.update(bindings=r["bindings"][:-1]), "Missing public"),
        (lambda r: r.update(bindings=r["bindings"][1:]), "Missing required"),
        (lambda r: r["budget"].update(steps=1), "aggregate budget"),
        (lambda r: r.update(effects=["write"]), "effects"),
        (lambda r: r["bindings"][0].update(**{"from": "outputs.text"}), "direction"),
    ],
)
def test_invalid_composition_is_not_published(tmp_path, mutate, reason):
    versions = store(tmp_path)
    versions.register(leaf())
    root = parent([leaf(), leaf()])
    mutate(root)
    with pytest.raises(ValueError, match=reason):
        versions.register(root)
    with pytest.raises(ValueError, match="Unknown"):
        store(tmp_path).get("parent", 1)


def test_binding_cycle_and_disconnected_execution_are_rejected(tmp_path):
    versions = store(tmp_path)
    versions.register(leaf())
    root = parent([leaf(), leaf()])
    root["bindings"][0]["from"] = "c1.outputs.text"
    with pytest.raises(ValueError, match="cycle"):
        versions.register(root)
    root = parent([leaf(), leaf()])
    root["bindings"] = [
        {"from": "inputs.text", "to": "c0.inputs.text"},
        {"from": "inputs.text", "to": "c1.inputs.text"},
        {"from": "c0.outputs.text", "to": "outputs.text"},
    ]
    with pytest.raises(ValueError, match="Disconnected"):
        versions.register(root)


def test_expansion_is_bounded_before_execution(tmp_path):
    versions = store(tmp_path)
    current = leaf()
    versions.register(current)
    for i in range(8):
        current = parent([current], name=f"parent-{i}")
        versions.register(current)
    too_deep = parent([current], name="too-deep")
    with pytest.raises(ValueError, match="depth"):
        versions.register(too_deep)


def test_include_cycle_rejects_before_recurse():
    raw = parent([leaf()])
    root = Definition.model_validate(raw)
    # Malicious lookup cannot substitute a different root behind an exact child pin.
    with pytest.raises(ValueError, match="different identity"):
        Compiler(lambda *_: root, validate_fixture).compile(root)


@pytest.mark.parametrize(
    "change",
    [
        {"type": "boolean"},
        {"schema_revision": 2},
        {"max_bytes": 101},
        {"min_count": 0},
    ],
)
def test_port_compatibility_fails_closed(change):
    source = Port.model_validate(port(**change))
    with pytest.raises(ValueError):
        compatible(source, Port.model_validate(port()))


def test_media_mime_and_cardinality_constraints():
    source = Port.model_validate(
        port(type="asset", media_kind="audio", mime_types=["audio/wav", "audio/mpeg"], max_count=2)
    )
    target = Port.model_validate(port(type="asset", media_kind="audio", mime_types=["audio/wav"]))
    with pytest.raises(ValueError, match="cardinality"):
        compatible(source, target)
    narrower = source.model_copy(update={"max_count": 1})
    with pytest.raises(ValueError, match="MIME"):
        compatible(narrower, target)
    with pytest.raises(ValueError, match="managed-input"):
        source.validate_value("/private/path.wav")


@pytest.mark.parametrize("raw", [b'{"id":1,"id":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_noncanonical_json_is_rejected(raw):
    with pytest.raises(ValueError):
        decode_definition(raw)


def test_nesting_bytes_and_boolean_integer_are_bounded():
    value = {}
    for _ in range(34):
        value = {"nested": value}
    with pytest.raises(ValueError, match="structure"):
        canonical(value)
    with pytest.raises(ValueError, match="byte"):
        Port.model_validate(port()).validate_value("x" * 101)
    with pytest.raises(ValueError, match="type"):
        Port.model_validate(port(type="integer")).validate_value(True)


def test_unknown_profile_artifact_and_schema_never_confer_readiness(tmp_path):
    versions = store(tmp_path)
    raw = leaf(profile={"id": "unknown", "revision": 1})
    with pytest.raises(ValueError, match="profile"):
        versions.register(raw)
    raw = leaf()
    raw["execution"]["artifact"] = {"command": "shell"}
    with pytest.raises(ValueError, match="artifact"):
        versions.register(raw)
    raw = leaf(schema_version=4)
    with pytest.raises(ValueError):
        versions.register(raw)
    result = versions.register(leaf())
    assert result["readiness"]["state"] == "validated"
    assert "artifact" not in result and "execution" not in result


def test_failed_publication_leaves_live_and_disk_unchanged(tmp_path, monkeypatch):
    versions = store(tmp_path)
    versions.register(leaf())

    def fail(*_):
        raise OSError("disk failed")

    monkeypatch.setattr(versions.records, "write", fail)
    with pytest.raises(OSError):
        versions.register(leaf(version=2))
    assert versions.get("child").version == 1
    assert store(tmp_path).get("child").version == 1


def test_uncertain_publication_blocks_every_live_operation(tmp_path, monkeypatch):
    versions = store(tmp_path)
    result = versions.register(leaf())

    def uncertain(*_):
        raise CommitUnknown("uncertain")

    monkeypatch.setattr(versions.records, "write", uncertain)
    with pytest.raises(CommitUnknown):
        versions.register(leaf(version=2))
    with pytest.raises(ValueError, match="uncertain"):
        versions.get("child")
    with pytest.raises(ValueError, match="uncertain"):
        versions.compile("child", 1, result["digest"])


def test_symlink_store_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "definition-versions-v3").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        store(tmp_path)


def test_optional_defaults_are_bound_and_numbers_normalize(tmp_path):
    versions = store(tmp_path)
    raw = leaf(inputs={"text": port(min_count=0, default="hello")})
    result = versions.register(raw)
    plan = versions.compile("child", 1, result["digest"])
    assert plan.bind({})["inputs"] == {"text": "hello"}
    a = Port.model_validate(port(type="number", min_count=0, default=1))
    b = Port.model_validate(port(type="number", min_count=0, default=1.0))
    assert canonical(a.model_dump()) == canonical(b.model_dump())


def test_occurrence_limit_counts_nested_repeated_aliases(tmp_path):
    versions = store(tmp_path)
    versions.register(leaf())
    nested = parent([leaf()], name="nested")
    versions.register(nested)
    # 64 root edges plus one nested edge must reject before the extra allocation.
    too_many = parent([nested, *[leaf() for _ in range(63)]])
    with pytest.raises(ValueError, match="occurrence"):
        versions.register(too_many)


def test_store_capacity_never_evicts_a_child(tmp_path, monkeypatch):
    import flamoris_generation_mcp.definition_versions as module

    versions = store(tmp_path)
    child = versions.register(leaf())
    root = versions.register(parent([leaf()]))
    monkeypatch.setattr(module, "MAX_RECORDS", 2)
    with pytest.raises(ValueError, match="full"):
        versions.register(leaf(version=2))
    assert versions.get("child", 1).digest == child["digest"]
    assert versions.compile("parent", 1, root["digest"])


def test_concurrent_publications_do_not_drop_records(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    versions = store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: versions.register(leaf(name=f"child-{i}")), range(16)))
    restarted = store(tmp_path)
    assert all(restarted.get(f"child-{i}").version == 1 for i in range(16))


def test_canonical_utf8_fixture_and_large_keys():
    assert canonical({"z": 1, "a": "愛乃"}) == '{"a":"愛乃","z":1}'.encode()
    with pytest.raises(ValueError, match="byte"):
        canonical({"x" * 101: 1}, limit=100)


def test_effects_and_role_semantics_are_checked(tmp_path):
    versions = store(tmp_path)
    raw = leaf(effects=["paid", "external"])
    equivalent = leaf(effects=["external", "paid"])
    assert Definition.model_validate(raw).digest == Definition.model_validate(equivalent).digest
    with pytest.raises(ValueError):
        versions.register(leaf(effects=["pure", "paid"]))
    with pytest.raises(ValueError):
        versions.register(leaf(effects=["destructive"]))
    with pytest.raises(ValueError, match="type/schema/media"):
        compatible(
            Port.model_validate(port(role="lyrics")), Port.model_validate(port(role="style"))
        )
