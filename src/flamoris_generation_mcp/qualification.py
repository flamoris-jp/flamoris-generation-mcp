"""Observe one exact Workflow verification through an already-running MCP server.

This client never grants readiness, registers graphs, retries admission or starts
a second Generation authority. Receipts are observations, not attestations.
"""

import argparse
import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

MAX_BYTES = 1024 * 1024
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
HASH = re.compile(r"sha256:[0-9a-f]{64}")
JOB = re.compile(r"[0-9a-f]{32}")


class ObservationError(Exception):
    """An allowlisted outcome, never an upstream exception or payload."""


def identity(value, *, v3=False):
    keys = ("id", "version", "digest") if v3 else ("id", "definition_version", "definition_digest")
    observed = [value[key] for key in keys]
    if (
        not isinstance(observed[0], str)
        or not ID.fullmatch(observed[0])
        or type(observed[1]) is not int
        or observed[1] < 1
        or not isinstance(observed[2], str)
        or not HASH.fullmatch(observed[2])
    ):
        raise ObservationError("catalog_unavailable")
    return dict(
        zip(("workflow_id", "definition_version", "definition_digest"), observed, strict=True)
    )


def bounded_json(raw):
    def duplicate_free(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def nonfinite(_value):
        raise ValueError("Nonfinite JSON value")

    return json.loads(raw, object_pairs_hook=duplicate_free, parse_constant=nonfinite)


def configuration(args):
    url = urlsplit(args.url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or not ID.fullmatch(args.workflow_id)
        or not HASH.fullmatch(args.definition_digest)
        or not 1 <= args.definition_version <= 2**53 - 1
        or not 1 <= args.timeout <= 330
        or not re.fullmatch(r"(?:[a-z][a-z0-9_-]{0,63}\.)?", args.tool_prefix)
    ):
        raise ValueError("Invalid qualification configuration")
    with args.parameters.open("rb") as source:
        raw = source.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError("Parameter file exceeds bound")
    parameters = bounded_json(raw)
    if (
        not isinstance(parameters, dict)
        or len(parameters) > 32
        or any(
            not ID.fullmatch(key)
            or type(value) not in (str, int, float, bool)
            or (isinstance(value, str) and len(value) > 20000)
            or (type(value) is float and not math.isfinite(value))
            for key, value in parameters.items()
        )
    ):
        raise ValueError("Parameters require bounded scalar values")
    headers = {}
    if args.headers_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.headers_env):
            raise ValueError("Invalid header environment reference")
        raw = os.environ[args.headers_env]
        if len(raw.encode()) > 16 * 1024:
            raise ValueError("Headers exceed bound")
        headers = bounded_json(raw)
        reserved = {
            "host",
            "content-length",
            "content-type",
            "accept",
            "accept-encoding",
            "connection",
            "transfer-encoding",
        }
        if (
            not isinstance(headers, dict)
            or len(headers) > 16
            or len({key.lower() for key in headers}) != len(headers)
            or any(
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", key)
                or key.lower() in reserved
                or key.lower().startswith("mcp-")
                or not isinstance(value, str)
                or not re.fullmatch(r"[\x20-\x7e]{1,4096}", value)
                for key, value in headers.items()
            )
        ):
            raise ValueError("Invalid authentication headers")
    return parameters, headers


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > MAX_BYTES:
                raise ObservationError("response_unavailable")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


async def bound_response(response):
    # MCP JSON decoding reads a whole body. Request uncompressed bodies and
    # reject unsolicited compression before any decompression can expand it.
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise ObservationError("response_unavailable")
    if response.is_stream_consumed and len(response.content) > MAX_BYTES:
        raise ObservationError("response_unavailable")
    response.stream = BoundedStream(response.stream)


class Receipt:
    def __init__(self, expected, *, v3=False, execute=False, path=None):
        self.path = path
        self.data = {
            "schema_version": 1,
            "observation_only": True,
            "workflow": expected,
            "catalog": "v3" if v3 else "v2",
            "execution_requested": execute,
            "outcome": "started",
            "observed_at": time.time(),
        }
        if path is not None:
            # Refuse existing files before any upstream call. Never overwrite
            # another observation or follow a pre-existing symlink.
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
        self.save()

    def save(self):
        self.data["observed_at"] = time.time()
        if self.path is None:
            return
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix=".qualification-")
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(self.data, output, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)


async def qualify(client, receipt, parameters, *, prefix="", timeout=330, poll_interval=1):
    """One admission at most; any unknown outcome requires operator reconciliation."""
    expected = receipt.data["workflow"]
    v3 = receipt.data["catalog"] == "v3"
    list_tool = "workflows.v3.list" if v3 else "workflows.list"
    verify_tool = "workflows.v3.verify" if v3 else "workflows.verify"
    phase = "preflight"

    async def call(name, arguments=None):
        # The high-level Client convenience method can replay tools/call for
        # input-required/header-mismatch recovery. The session primitive sends
        # one request and rejects input-required without resending it.
        result = await client.session.call_tool(prefix + name, arguments, read_timeout_seconds=30)
        if result.is_error:
            raise ObservationError("tool_unavailable")
        data = result.structured_content
        if (
            not isinstance(data, dict)
            or len(json.dumps(data, allow_nan=False).encode()) > MAX_BYTES
        ):
            raise ObservationError("response_unavailable")
        return data

    async def descriptor():
        catalog = await call(list_tool)
        entries = catalog.get("descriptors" if v3 else "definitions")
        if not isinstance(entries, list) or len(entries) > 128:
            raise ObservationError("catalog_unavailable")
        matches = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id") == expected["workflow_id"]
        ]
        if len(matches) != 1 or identity(matches[0], v3=v3) != expected:
            raise ObservationError("definition_changed")
        item = matches[0]
        if not v3 and item.get("metadata_schema_version") != 2:
            raise ObservationError("profile_unavailable")
        readiness = item.get("readiness", {}).get("state")
        if readiness not in {"registered", "validated", "ready"}:
            raise ObservationError("catalog_unavailable")
        receipt.data["workflow_readiness"] = readiness
        return item

    def observe_job(data, job_id=None, attempt_id=None):
        observed = data.get("job_id")
        if (
            not isinstance(observed, str)
            or not JOB.fullmatch(observed)
            or (job_id and observed != job_id)
        ):
            raise ObservationError("job_identity_unavailable")
        receipt.data["job_id"] = observed
        verification = data.get("verification")
        if not isinstance(verification, dict) or not isinstance(verification.get("identity"), dict):
            raise ObservationError("verification_unavailable")
        if type(verification["identity"].get("definition_version")) is not int or any(
            verification.get("identity", {}).get(key) != value for key, value in expected.items()
        ):
            raise ObservationError("verification_unavailable")
        attempt = verification.get("attempt_id")
        if (
            not isinstance(attempt, str)
            or not JOB.fullmatch(attempt)
            or (attempt_id and attempt_id != attempt)
        ):
            raise ObservationError("verification_unavailable")
        state = verification.get("state")
        status = data.get("status")
        if state not in {"pending", "ready", "failed"} or status not in {
            "queued",
            "running",
            "completed",
            "failed",
            "cancel_requested",
            "cancelled",
            "unknown",
        }:
            raise ObservationError("verification_unavailable")
        receipt.data.update(attempt_id=attempt, verification_state=state, job_status=status)
        return verification

    try:
        async with asyncio.timeout(timeout):
            health = await call("system.health")
            providers = health.get("providers", [])
            available = any(
                isinstance(item, dict)
                and item.get("id") == "comfyui"
                and item.get("available") is True
                for item in providers
            )
            receipt.data["availability"] = {
                "provider_available": available,
                "busy": health.get("busy") is True,
                "managed_input_ready": health.get("managed_input_support", {}).get("ready") is True,
            }
            await descriptor()
            if health.get("healthy") is not True or not available:
                raise ObservationError("provider_unavailable")
            if health.get("busy") is not False:
                raise ObservationError("generation_busy")
            receipt.data["outcome"] = "preflight_passed"
            receipt.save()
            if not receipt.data["execution_requested"]:
                return receipt.data
            phase = "admission"
            receipt.data["phase"] = phase
            receipt.data["preflight_readiness"] = receipt.data.pop("workflow_readiness")
            receipt.data["outcome"] = "admission_requested"
            receipt.save()
            admitted = await call(verify_tool, {**expected, "parameters": parameters})
            observe_job(admitted)
            phase = "observation"
            receipt.data["phase"] = phase
            receipt.data["outcome"] = "observing"
            receipt.save()  # Preserve the accepted job ID before the first await.
            job_id, attempt_id = receipt.data["job_id"], receipt.data["attempt_id"]
            while True:
                data = await call("jobs.status", {"job_id": job_id})
                verification = observe_job(data, job_id, attempt_id)
                if data["status"] == "unknown":
                    raise ObservationError("submission_unresolved")
                if verification["state"] == "failed" or data["status"] in {"failed", "cancelled"}:
                    raise ObservationError("verification_failed")
                if data["status"] == "completed" and verification["state"] == "ready":
                    result = await call("jobs.result", {"job_id": job_id})
                    verification = observe_job(result, job_id, attempt_id)
                    if result["status"] != "completed" or verification["state"] != "ready":
                        raise ObservationError("verification_unavailable")
                    output = verification.get("output", {})
                    if (
                        any(
                            type(output.get(key)) is not int or not 64 <= output[key] <= 512
                            for key in ("width", "height")
                        )
                        or not isinstance(output.get("digest"), str)
                        or not HASH.fullmatch(output["digest"])
                    ):
                        raise ObservationError("output_evidence_unavailable")
                    receipt.data["output"] = {
                        key: output[key] for key in ("width", "height", "digest")
                    }
                    item = await descriptor()
                    if receipt.data["workflow_readiness"] != "ready":
                        raise ObservationError("current_readiness_unavailable")
                    # Independent infrastructure availability remains visible;
                    # neither this receipt nor a smoke proves production admission.
                    receipt.data["managed_input_required"] = (
                        item.get("image", {}).get("mode") == "img2img"
                    )
                    receipt.data["outcome"] = "verified"
                    receipt.data["phase"] = "complete"
                    receipt.save()
                    return receipt.data
                await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        receipt.data["outcome"] = "submission_unresolved" if phase == "admission" else "interrupted"
        receipt.save()
        raise
    except Exception as exc:
        receipt.data["outcome"] = (
            "submission_unresolved"
            if phase == "admission"
            else "observation_timeout"
            if isinstance(exc, TimeoutError)
            else str(exc)
            if isinstance(exc, ObservationError)
            else "observation_unavailable"
        )
        receipt.save()
        return receipt.data


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "Invalid qualification arguments; use --help.\n")


@contextmanager
def quiet_logs():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def main(argv=None):
    # SDK exception logs can contain endpoint/header/provider data. This scope
    # applies to the standalone command and restores an embedding caller's logs.
    with quiet_logs():
        return _main(argv)


def _main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument(
        "--url", required=True, help="Existing MCP endpoint, without credentials/query"
    )
    parser.add_argument(
        "--tool-prefix", default="", help="Empty for Generation; generation. for Hub"
    )
    parser.add_argument(
        "--headers-env", help="Environment variable containing private JSON HTTP headers"
    )
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--definition-version", type=int, required=True)
    parser.add_argument("--definition-digest", required=True)
    parser.add_argument(
        "--parameters", type=Path, required=True, help="Private bounded scalar JSON file"
    )
    parser.add_argument("--v3", action="store_true")
    parser.add_argument(
        "--execute", action="store_true", help="Admit one smoke; default is read-only"
    )
    parser.add_argument("--timeout", type=int, default=330, help="Overall seconds, 1..330")
    parser.add_argument("--receipt", type=Path, help="New private observation file; must not exist")
    args = parser.parse_args(argv)
    receipt = None
    try:
        parameters, headers = configuration(args)
        expected = {
            "workflow_id": args.workflow_id,
            "definition_version": args.definition_version,
            "definition_digest": args.definition_digest,
        }
        receipt = Receipt(expected, v3=args.v3, execute=args.execute, path=args.receipt)

        async def run():
            async with asyncio.timeout(args.timeout):
                async with httpx2.AsyncClient(
                    headers={"accept-encoding": "identity", **headers},
                    trust_env=False,
                    timeout=30,
                    event_hooks={"response": [bound_response]},
                ) as http:
                    transport = streamable_http_client(
                        args.url, http_client=http, max_sse_event_size=MAX_BYTES
                    )
                    async with Client(transport, read_timeout_seconds=30, cache=None) as client:
                        return await qualify(
                            client,
                            receipt,
                            parameters,
                            prefix=args.tool_prefix,
                            timeout=args.timeout,
                        )

        result = asyncio.run(run())
    except KeyboardInterrupt:
        result = receipt.data if receipt else {"observation_only": True, "outcome": "interrupted"}
        if receipt and result.get("phase") != "complete":
            result["outcome"] = (
                "submission_unresolved" if result.get("phase") == "admission" else "interrupted"
            )
            try:
                receipt.save()
            except OSError:
                pass
        print(json.dumps(result, allow_nan=False))
        return 130
    except Exception as exc:
        result = receipt.data if receipt else {"observation_only": True}
        result["outcome"] = (
            "submission_unresolved"
            if receipt and result.get("phase") == "admission"
            else "observation_timeout"
            if receipt and isinstance(exc, TimeoutError)
            else "connection_unavailable"
            if receipt
            else "configuration_unavailable"
        )
        if receipt:
            try:
                receipt.save()
            except OSError:
                result["outcome"] = "receipt_unavailable"
    print(json.dumps(result, allow_nan=False))
    return 0 if result["outcome"] in {"preflight_passed", "verified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
