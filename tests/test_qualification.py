import asyncio
import copy
import json
from types import SimpleNamespace

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from flamoris_generation_mcp.qualification import (
    MAX_BYTES,
    Receipt,
    bound_response,
    configuration,
    main,
    qualify,
)

EXPECTED = {
    "workflow_id": "image-test",
    "definition_version": 1,
    "definition_digest": "sha256:" + "a" * 64,
}
JOB_ID, ATTEMPT_ID = "b" * 32, "c" * 32
PRIVATE = "private-token-prompt-host-path"


def descriptor():
    return {
        "id": EXPECTED["workflow_id"],
        **{key: value for key, value in EXPECTED.items() if key != "workflow_id"},
        "metadata_schema_version": 2,
        "image": {"mode": "txt2img"},
        "readiness": {"state": "validated"},
        "description": PRIVATE,
    }


def job(status="queued", state="pending"):
    return {
        "job_id": JOB_ID,
        "status": status,
        "provider_execution_id": PRIVATE,
        "files": [{"file": PRIVATE}],
        "verification": {
            "identity": {**EXPECTED, "profile_revision": 2},
            "attempt_id": ATTEMPT_ID,
            "state": state,
            "runtime": {"manifest": PRIVATE},
            "output": {"width": 512, "height": 512, "digest": "sha256:" + "d" * 64},
        },
    }


class Endpoint:
    def __init__(self, *, prefix="", v3=False):
        self.calls = []
        self.session = self
        self.item = descriptor()
        self.status = job("completed", "ready")
        self.available = True
        self.prefix = prefix
        self.v3 = v3
        if v3:
            self.item.update(
                version=EXPECTED["definition_version"], digest=EXPECTED["definition_digest"]
            )

    async def call_tool(self, name, arguments=None, **_kwargs):
        assert name.startswith(self.prefix)
        short = name.removeprefix(self.prefix)
        self.calls.append((short, copy.deepcopy(arguments)))
        if short == "system.health":
            data = {
                "healthy": True,
                "busy": False,
                "providers": [{"id": "comfyui", "available": self.available}],
                "managed_input_support": {"ready": False},
            }
        elif short in {"workflows.list", "workflows.v3.list"}:
            data = {"descriptors" if self.v3 else "definitions": [copy.deepcopy(self.item)]}
        elif short in {"workflows.verify", "workflows.v3.verify"}:
            self.item["readiness"]["state"] = "ready"
            data = job()
        else:
            assert short in {"jobs.status", "jobs.result"}
            data = self.status
        return SimpleNamespace(is_error=False, structured_content=copy.deepcopy(data))


async def test_preflight_is_read_only_and_receipt_is_private(tmp_path):
    endpoint = Endpoint()
    path = tmp_path / "receipt.json"
    receipt = Receipt(EXPECTED, path=path)
    result = await qualify(endpoint, receipt, {"positive_prompt": PRIVATE})
    assert result["outcome"] == "preflight_passed"
    assert [name for name, _ in endpoint.calls] == ["system.health", "workflows.list"]
    assert PRIVATE not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        Receipt(EXPECTED, path=path)


@pytest.mark.parametrize("v3,prefix", [(False, ""), (False, "generation."), (True, "generation.")])
async def test_one_admission_and_exact_ready_recheck(v3, prefix):
    endpoint = Endpoint(v3=v3, prefix=prefix)
    receipt = Receipt(EXPECTED, v3=v3, execute=True)
    result = await qualify(endpoint, receipt, {"positive_prompt": PRIVATE}, prefix=prefix)
    assert result["outcome"] == "verified"
    assert result["job_id"] == JOB_ID and result["attempt_id"] == ATTEMPT_ID
    assert PRIVATE not in json.dumps(result)
    assert result["availability"]["managed_input_ready"] is False
    verify = "workflows.v3.verify" if v3 else "workflows.verify"
    assert [name for name, _ in endpoint.calls].count(verify) == 1
    assert endpoint.calls[-1][0] == ("workflows.v3.list" if v3 else "workflows.list")
    assert not any(
        name in {"jobs.submit", "jobs.cancel", "workflows.register"} for name, _ in endpoint.calls
    )


@pytest.mark.parametrize(
    "change", ["version", "digest", "boolean_version", "duplicate", "provider"]
)
async def test_preflight_failure_never_admits(change):
    endpoint = Endpoint()
    if change == "version":
        endpoint.item["definition_version"] = 2
    elif change == "boolean_version":
        endpoint.item["definition_version"] = True
    elif change == "digest":
        endpoint.item["definition_digest"] = "sha256:" + "f" * 64
    elif change == "provider":
        endpoint.available = False
    else:
        original = endpoint.call_tool

        async def duplicated(*args, **kwargs):
            result = await original(*args, **kwargs)
            if args[0] == "workflows.list":
                result.structured_content["definitions"].append(endpoint.item)
            return result

        endpoint.call_tool = duplicated
    result = await qualify(endpoint, Receipt(EXPECTED, execute=True), {})
    assert result["outcome"] not in {"preflight_passed", "verified"}
    assert not any(name == "workflows.verify" for name, _ in endpoint.calls)


@pytest.mark.parametrize("v3,value", [(False, True), (False, 1.0), (True, True), (True, 1.0)])
async def test_exact_catalog_version_rejects_boolean_and_float(v3, value):
    endpoint = Endpoint(v3=v3)
    endpoint.item["version" if v3 else "definition_version"] = value
    result = await qualify(endpoint, Receipt(EXPECTED, execute=True, v3=v3), {})
    assert result["outcome"] == "catalog_unavailable"
    assert not any(name.endswith(".verify") for name, _ in endpoint.calls)


@pytest.mark.parametrize("change", ["unknown", "failed", "job", "attempt", "runtime", "version"])
async def test_failed_or_superseded_verification_never_succeeds(change):
    endpoint = Endpoint()
    if change == "unknown":
        endpoint.status = job("unknown", "failed")
    elif change == "failed":
        endpoint.status = job("failed", "failed")
    elif change == "job":
        endpoint.status["job_id"] = "e" * 32
    elif change == "attempt":
        endpoint.status["verification"]["attempt_id"] = "e" * 32
    else:
        original = endpoint.call_tool

        async def changed(*args, **kwargs):
            result = await original(*args, **kwargs)
            if args[0] == "jobs.result":
                if change == "runtime":
                    endpoint.item["readiness"]["state"] = "validated"
                else:
                    endpoint.item["definition_version"] = 2
            return result

        endpoint.call_tool = changed
    result = await qualify(endpoint, Receipt(EXPECTED, execute=True), {})
    assert result["outcome"] != "verified"
    assert [name for name, _ in endpoint.calls].count("workflows.verify") == 1
    assert not any(name == "jobs.cancel" for name, _ in endpoint.calls)


async def test_admission_error_never_retries_or_exposes_details():
    endpoint = Endpoint()
    original = endpoint.call_tool

    async def uncertain(name, *args, **kwargs):
        if name == "workflows.verify":
            endpoint.calls.append((name, None))
            raise RuntimeError(PRIVATE)
        return await original(name, *args, **kwargs)

    endpoint.call_tool = uncertain
    result = await qualify(endpoint, Receipt(EXPECTED, execute=True), {})
    assert result["outcome"] == "submission_unresolved"
    assert PRIVATE not in json.dumps(result)
    assert [name for name, _ in endpoint.calls].count("workflows.verify") == 1


async def test_observation_timeout_keeps_accepted_job_receipt(tmp_path):
    endpoint = Endpoint()
    endpoint.status = job()
    path = tmp_path / "receipt.json"
    result = await qualify(endpoint, Receipt(EXPECTED, execute=True, path=path), {}, timeout=0.01)
    assert result["outcome"] == "observation_timeout"
    assert json.loads(path.read_text())["job_id"] == JOB_ID
    assert [name for name, _ in endpoint.calls].count("workflows.verify") == 1


async def test_actual_mcp_protocol_preflight(settings, fake, tmp_path):
    import httpx
    from test_workflow_v2 import definition

    from flamoris_generation_mcp.server import create_server

    isolated = settings.model_copy(update={"workflow_definition_dir": tmp_path / "definitions"})
    server = create_server(isolated, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        registered = await client.call_tool("workflows.register", {"definition": definition()})
        expected = {
            "workflow_id": "image-v2",
            "definition_version": registered.structured_content["definition_version"],
            "definition_digest": registered.structured_content["definition_digest"],
        }
        result = await qualify(client, Receipt(expected), {"positive_prompt": PRIVATE})
    assert result["outcome"] == "preflight_passed"
    assert not fake.prompts


@pytest.mark.parametrize("reply", ["input_required", "header_mismatch"])
async def test_actual_sdk_never_replays_admission(reply):
    from mcp import types
    from mcp.server import Server
    from mcp.shared.exceptions import MCPError
    from mcp_types.jsonrpc import HEADER_MISMATCH

    endpoint = Endpoint()

    async def invoke(_context, params):
        if params.name == "workflows.verify":
            endpoint.calls.append((params.name, params.arguments))
            if reply == "input_required":
                return types.InputRequiredResult(requestState=PRIVATE)
            raise MCPError(HEADER_MISMATCH, PRIVATE)
        response = await endpoint.call_tool(params.name, params.arguments)
        return types.CallToolResult(content=[], structuredContent=response.structured_content)

    async def listing(_context, _params):
        return types.ListToolsResult(
            tools=[
                types.Tool(name=name, inputSchema={"type": "object"})
                for name in ("system.health", "workflows.list", "workflows.verify")
            ]
        )

    server = Server("qualification-negative", on_call_tool=invoke, on_list_tools=listing)
    async with Client(server, cache=None) as client:
        result = await qualify(client, Receipt(EXPECTED, execute=True), {})
    assert result["outcome"] == "submission_unresolved"
    assert PRIVATE not in json.dumps(result)
    assert [name for name, _ in endpoint.calls].count("workflows.verify") == 1


def arguments(tmp_path, monkeypatch, **changes):
    source = tmp_path / "parameters.json"
    source.write_text(json.dumps({"positive_prompt": PRIVATE}))
    monkeypatch.setenv("PRIVATE_HEADERS", json.dumps({"Authorization": "Bearer " + PRIVATE}))
    return SimpleNamespace(
        **{
            "url": "https://example.invalid/mcp",
            "workflow_id": EXPECTED["workflow_id"],
            "definition_version": 1,
            "definition_digest": EXPECTED["definition_digest"],
            "tool_prefix": "generation.",
            "timeout": 330,
            "parameters": source,
            "headers_env": "PRIVATE_HEADERS",
            **changes,
        }
    )


@pytest.mark.parametrize("suffix", ["?token=secret", "#secret"])
def test_credentials_in_url_rejected(tmp_path, monkeypatch, suffix):
    with pytest.raises(ValueError):
        configuration(arguments(tmp_path, monkeypatch, url="https://example.invalid/mcp" + suffix))
    with pytest.raises(ValueError):
        configuration(
            arguments(tmp_path, monkeypatch, url="https://user:secret@example.invalid/mcp")
        )


def test_parameter_duplicates_and_unsafe_headers_rejected(tmp_path, monkeypatch):
    args = arguments(tmp_path, monkeypatch)
    parameters, headers = configuration(args)
    assert parameters["positive_prompt"] == PRIVATE and PRIVATE in headers["Authorization"]
    args.parameters.write_text('{"seed":0,"seed":1}')
    with pytest.raises(ValueError):
        configuration(args)
    args.parameters.write_text('{"seed":0}')
    for headers in (
        {"Host": "wrong"},
        {"Authorization": "x\ny"},
        {"Authorization": "x", "authorization": "y"},
    ):
        monkeypatch.setenv("PRIVATE_HEADERS", json.dumps(headers))
        with pytest.raises(ValueError):
            configuration(args)


async def test_actual_sdk_response_bound_and_compression_rejection():
    # The actual MCP SDK reads JSON bodies eagerly. Bound the raw response and
    # reject compression before its decoder can expand attacker-controlled data.
    for headers, body in [
        ({"content-type": "application/json"}, b"x" * (MAX_BYTES + 1)),
        ({"content-type": "application/json", "content-encoding": "gzip"}, b"bad"),
    ]:
        transport = httpx2.MockTransport(
            lambda _request, headers=headers, body=body: httpx2.Response(
                200, headers=headers, stream=httpx2.ByteStream(body)
            )
        )
        async with httpx2.AsyncClient(
            transport=transport, event_hooks={"response": [bound_response]}
        ) as http:
            with pytest.raises((ExceptionGroup, RuntimeError, TimeoutError)):
                async with Client(
                    streamable_http_client("https://example.invalid/mcp", http_client=http),
                    read_timeout_seconds=0.1,
                    cache=None,
                ):
                    pass


async def test_preconsumed_body_is_also_bounded():
    from flamoris_generation_mcp.qualification import ObservationError

    response = httpx2.Response(200, content=b"x" * (MAX_BYTES + 1))
    with pytest.raises(ObservationError, match="response_unavailable"):
        await bound_response(response)


def test_cli_configuration_error_is_sanitized(tmp_path, capsys):
    source = tmp_path / "parameters.json"
    source.write_text("{}")
    status = main(
        [
            "--url",
            "https://user:" + PRIVATE + "@example.invalid/mcp",
            "--workflow-id",
            EXPECTED["workflow_id"],
            "--definition-version",
            "1",
            "--definition-digest",
            EXPECTED["definition_digest"],
            "--parameters",
            str(source),
        ]
    )
    assert status == 1
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err
    assert json.loads(output.out)["outcome"] == "configuration_unavailable"


def test_actual_sdk_malformed_reply_does_not_log_secrets(tmp_path, monkeypatch, capsys):
    args = arguments(tmp_path, monkeypatch)
    original_client = httpx2.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            200, headers={"content-type": "application/json"}, json={"private": PRIVATE}
        )

    def network(*values, **kwargs):
        return original_client(*values, transport=httpx2.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", network)
    status = main(
        [
            "--url",
            args.url,
            "--headers-env",
            "PRIVATE_HEADERS",
            "--workflow-id",
            EXPECTED["workflow_id"],
            "--definition-version",
            "1",
            "--definition-digest",
            EXPECTED["definition_digest"],
            "--parameters",
            str(args.parameters),
        ]
    )
    assert status == 1 and requests
    assert requests[0].headers["Authorization"] == "Bearer " + PRIVATE
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert "example.invalid" not in captured.out + captured.err
    assert json.loads(captured.out)["outcome"] == "connection_unavailable"


def test_outer_deadline_during_admission_is_unresolved(tmp_path, monkeypatch, capsys):
    args = arguments(tmp_path, monkeypatch)
    endpoint = Endpoint()
    original = endpoint.call_tool

    async def stalled(name, *values, **kwargs):
        if name == "workflows.verify":
            endpoint.calls.append((name, None))
            await asyncio.Future()
        return await original(name, *values, **kwargs)

    endpoint.call_tool = stalled

    class Context:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return endpoint

        async def __aexit__(self, *_args):
            return False

    import flamoris_generation_mcp.qualification as module

    original_timeout = module.asyncio.timeout
    monkeypatch.setattr(module.asyncio, "timeout", lambda _seconds: original_timeout(0.01))
    monkeypatch.setattr(module, "Client", Context)
    receipt_path = tmp_path / "receipt.json"
    status = main(
        [
            "--url",
            args.url,
            "--workflow-id",
            EXPECTED["workflow_id"],
            "--definition-version",
            "1",
            "--definition-digest",
            EXPECTED["definition_digest"],
            "--parameters",
            str(args.parameters),
            "--execute",
            "--receipt",
            str(receipt_path),
        ]
    )
    assert status == 1
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "submission_unresolved"
    assert json.loads(receipt_path.read_text())["outcome"] == "submission_unresolved"
    assert [name for name, _ in endpoint.calls].count("workflows.verify") == 1


def test_cleanup_timeout_preserves_accepted_observation(tmp_path, monkeypatch, capsys):
    args = arguments(tmp_path, monkeypatch)
    endpoint = Endpoint()

    class Context:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return endpoint

        async def __aexit__(self, *_args):
            await asyncio.Future()

    import flamoris_generation_mcp.qualification as module

    original_timeout = module.asyncio.timeout
    monkeypatch.setattr(module.asyncio, "timeout", lambda _seconds: original_timeout(0.02))
    monkeypatch.setattr(module, "Client", Context)
    receipt_path = tmp_path / "receipt.json"
    status = main(
        [
            "--url",
            args.url,
            "--workflow-id",
            EXPECTED["workflow_id"],
            "--definition-version",
            "1",
            "--definition-digest",
            EXPECTED["definition_digest"],
            "--parameters",
            str(args.parameters),
            "--execute",
            "--receipt",
            str(receipt_path),
        ]
    )
    assert status == 1
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "observation_timeout"
    assert result["job_id"] == JOB_ID and result["attempt_id"] == ATTEMPT_ID
    assert json.loads(receipt_path.read_text())["job_id"] == JOB_ID
    assert [name for name, _ in endpoint.calls].count("workflows.verify") == 1
