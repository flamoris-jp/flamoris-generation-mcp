"""Non-secret external client provenance, separate from asset ownership."""

import hashlib
import hmac
import json
import math
import re
import time
from contextvars import ContextVar

from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .asset_files import AssetFiles
from .durable import Records

META_KEY = "flamoris.dev/external-provenance"
MAX_SIGNED_BYTES = 256 * 1024
MAX_NONCES = 4096
_current: ContextVar["ExternalProvenance | None"] = ContextVar("external_provenance", default=None)


class ExternalProvenance(BaseModel):
    """Immutable provenance captured by trusted ingress, never public tool arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issuer: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    subject: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def provenance_metadata(value: ExternalProvenance | None) -> dict:
    return {"external_provenance": value.model_dump()} if value is not None else {}


def archived_provenance(record: dict) -> dict:
    """Legacy absence is anonymous; malformed new provenance cannot become anonymous."""
    if "external_provenance" not in record:
        return {}
    try:
        value = ExternalProvenance.model_validate(record["external_provenance"])
    except (ValueError, TypeError):
        raise ValueError("Invalid archived external provenance") from None
    return provenance_metadata(value)


class Envelope(ExternalProvenance):
    version: StrictInt = Field(ge=1, le=1)
    issued_at: StrictInt
    nonce: str = Field(pattern=r"^[a-f0-9]{32}$")
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


def current_provenance() -> ExternalProvenance | None:
    """Only trusted middleware can populate this request-task-scoped value."""
    return _current.get()


class ProvenanceIngress:
    """Authenticate raw MCP call arguments once, before tool validation or effects."""

    def __init__(self, settings):
        self.secret = (
            settings.provenance_secret.get_secret_value().encode()
            if settings.provenance_secret is not None
            else None
        )
        self.issuer = settings.provenance_issuer
        self._journal = Records(settings.output_dir, "external-provenance")
        self._fenced = False
        self._seen_journal = False

    def _pristine_namespace(self) -> bool:
        try:
            with AssetFiles(self._journal.root, self._journal.namespace, create=False):
                return False
        except ValueError as exc:
            if str(exc) == "Unknown archived asset ID":
                return True
            raise

    def _admit_nonce(self, nonce: str) -> None:
        if self._fenced:
            raise ValueError("Untrusted external provenance context")
        try:
            now = time.time()
            if not math.isfinite(now) or now < 0:
                raise ValueError("Invalid provenance clock")
            record = self._journal.read("nonces.json")
            if record is None:
                if self._seen_journal or not self._pristine_namespace():
                    raise ValueError("Provenance journal disappeared")
                record = {"version": 1, "last_walltime": now, "nonces": {}}
            if (
                set(record) != {"version", "last_walltime", "nonces"}
                or type(record["version"]) is not int
                or record["version"] != 1
                or type(record["last_walltime"]) not in (int, float)
                or not math.isfinite(record["last_walltime"])
                or not 0 <= record["last_walltime"] <= now
                or type(record["nonces"]) is not dict
                or len(record["nonces"]) > MAX_NONCES
            ):
                raise ValueError("Invalid provenance nonce journal")
            live = {}
            for key, value in record["nonces"].items():
                if (
                    not re.fullmatch(r"[a-f0-9]{32}", key)
                    or type(value) is not dict
                    or set(value) != {"accepted_at", "expires_at"}
                    or any(type(value[name]) not in (int, float) for name in value)
                    or any(not math.isfinite(value[name]) for name in value)
                    or not 0 <= value["accepted_at"] <= record["last_walltime"]
                    or value["expires_at"] != value["accepted_at"] + 61
                ):
                    raise ValueError("Invalid provenance nonce journal")
                if value["expires_at"] > now:
                    live[key] = value
        except (ValueError, TypeError, OSError):
            self._fenced = True
            raise ValueError("Untrusted external provenance context") from None
        if nonce in live or len(live) >= MAX_NONCES:
            raise ValueError("Untrusted external provenance context")
        live[nonce] = {"accepted_at": now, "expires_at": now + 61}
        try:
            # Commit and fsync before the handler can execute any tool side effect.
            self._journal.write("nonces.json", {"version": 1, "last_walltime": now, "nonces": live})
        except (ValueError, OSError):
            self._fenced = True
            raise ValueError("Untrusted external provenance context") from None
        self._seen_journal = True

    def authenticate(self, meta, params) -> ExternalProvenance | None:
        if meta is not None and not isinstance(meta, dict):
            raise ValueError("Untrusted external provenance context")
        values = meta if meta is not None else {}
        if META_KEY not in values:
            return None
        if self.secret is None or self.issuer is None:
            raise ValueError("Untrusted external provenance context")
        envelope = Envelope.model_validate(values[META_KEY])
        if envelope.issuer != self.issuer or abs(time.time() - envelope.issued_at) > 30:
            raise ValueError("Untrusted external provenance context")
        if not isinstance(params, dict):
            raise ValueError("Untrusted external provenance context")
        tool = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool, str) or len(tool) > 128 or not isinstance(arguments, dict):
            raise ValueError("Untrusted external provenance context")
        signed = json.dumps(
            {
                **envelope.model_dump(exclude={"signature"}),
                "tool": tool,
                "arguments": arguments,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
        if len(signed) > MAX_SIGNED_BYTES or not hmac.compare_digest(
            hmac.new(self.secret, signed, hashlib.sha256).hexdigest(), envelope.signature
        ):
            raise ValueError("Untrusted external provenance context")
        # Durable bounded nonce admission also prevents replay across process restart.
        self._admit_nonce(envelope.nonce)
        return ExternalProvenance(issuer=envelope.issuer, subject=envelope.subject)

    async def __call__(self, ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)
        try:
            provenance = self.authenticate(ctx.meta, ctx.params)
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise MCPError(
                code=INVALID_PARAMS, message="Untrusted external provenance context"
            ) from None
        token = _current.set(provenance)
        try:
            return await call_next(ctx)
        finally:
            _current.reset(token)
