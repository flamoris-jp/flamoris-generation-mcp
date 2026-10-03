"""Private Runtime handoff ownership under the ordinary Generation reservation.

These are trusted in-process contracts, not portable authorization or MCP tools.
The embedding supplies authenticated peer mapping and registered dispatch guards.
No default bridge, provider registration or physical host authority is installed.
"""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .providers.base import GenerationRequest, JobSnapshot, SubmissionRejected
from .workflow_v3 import Contract, Digest, Identifier, digest

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Counter = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,19})$")]
Path = Annotated[str, Field(pattern=r"^root(?:/[a-z][a-z0-9_-]{0,63}){0,8}$")]
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:/-]+$")]


class RuntimeTarget(Contract):
    high: Counter
    low: Counter

    @model_validator(mode="after")
    def incarnation(self):
        if max(int(self.high), int(self.low)) > 2**64 - 1 or not (int(self.high) or int(self.low)):
            raise ValueError("Invalid Runtime incarnation")
        return self


class RuntimeAdmission(Contract):
    target: RuntimeTarget
    run: Counter
    request_digest: HexDigest
    plan_fingerprint: HexDigest

    @model_validator(mode="after")
    def run_counter(self):
        if not 1 <= int(self.run) <= 2**64 - 1:
            raise ValueError("Invalid Runtime Run identity")
        return self


class RuntimeStopReceipt(Contract):
    """Projection of the existing Runtime CommandReceipt, never release evidence."""

    admission: RuntimeAdmission
    accepted: bool
    applied: bool
    terminal: bool


class RuntimeObservation(Contract):
    """Bounded status projection; terminal lifecycle does not prove host settlement."""

    admission: RuntimeAdmission
    watermark: Counter
    state: Literal[
        "created",
        "queued",
        "running",
        "waiting",
        "paused",
        "cancelling",
        "finalizing",
        "succeeded",
        "failed",
        "cancelled",
    ]
    dispatch_open: bool
    child_creation_open: bool
    execution_in_flight: bool
    cleanup_pending: bool

    @model_validator(mode="after")
    def bounded_watermark(self):
        if int(self.watermark) > 2**64 - 1:
            raise ValueError("Invalid Runtime observation watermark")
        return self


class ProviderObservation(Contract):
    status: Literal[
        "queued", "running", "completed", "failed", "cancel_requested", "cancelled", "unknown"
    ]
    cancel_supported: bool | None = None


class RuntimeStopRecord(Contract):
    state: Literal["handoff_possible", "observed", "unknown"]
    receipt: RuntimeStopReceipt | None = None

    @model_validator(mode="after")
    def receipt_relation(self):
        if (self.state == "observed" and self.receipt is None) or (
            self.state == "handoff_possible" and self.receipt is not None
        ):
            raise ValueError("Invalid retained Runtime stop receipt")
        return self


class ProviderStopRecord(Contract):
    state: Literal["handoff_possible", "observed", "unknown"]
    receipt: ProviderObservation | None = None

    @model_validator(mode="after")
    def receipt_relation(self):
        if (self.state == "observed" and self.receipt is None) or (
            self.state == "handoff_possible" and self.receipt is not None
        ):
            raise ValueError("Invalid retained provider stop receipt")
        return self


class RuntimeCleanupRecord(Contract):
    stop: RuntimeStopRecord | None = None
    inspections: int = Field(default=0, ge=0, le=4)
    observation: RuntimeObservation | None = None
    last_failure: Literal["unknown", "observation_conflict"] | None = None

    @model_validator(mode="after")
    def inspected_observation(self):
        if self.observation is not None and not self.inspections:
            raise ValueError("Runtime observation requires a claimed inspection")
        return self


class ProviderCleanupRecord(Contract):
    stop: ProviderStopRecord | None = None
    inspections: int = Field(default=0, ge=0, le=4)
    observation: ProviderObservation | None = None
    last_failure: Literal["unknown", "observation_conflict"] | None = None

    @model_validator(mode="after")
    def inspected_observation(self):
        if self.observation is not None and not self.inspections:
            raise ValueError("Provider observation requires a claimed inspection")
        return self


class CleanupWindow(Contract):
    started_at: float = Field(gt=0, allow_inf_nan=False)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    closed: bool = False

    @model_validator(mode="after")
    def bounded_window(self):
        if self.expires_at - self.started_at != 30:
            raise ValueError("Owned cleanup requires one fixed 30-second window")
        return self


class Occurrence(Contract):
    path: Path
    workflow_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    capability: Annotated[str, Field(min_length=1, max_length=128)]
    fingerprint: HexDigest
    input_digest: Digest
    timeout_ms: int = Field(gt=0, le=60000)


class DelegationScope(Contract):
    principal: str = Field(min_length=1, max_length=128)
    target: RuntimeTarget
    root_digest: Digest
    closure_digest: Digest
    structural_digest: Digest
    invocation_digest: Digest
    evidence_digest: Digest
    root_request_digest: Digest
    runtime_request_digest: HexDigest
    runtime_plan_fingerprint: HexDigest
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    operations: tuple[Occurrence, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_occurrences(self):
        if len({x.path for x in self.operations}) != len(self.operations):
            raise ValueError("Duplicate delegated occurrence")
        return self


class OperationRecord(Contract):
    key: Key
    input_digest: Digest
    provider_job_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    state: Literal["handoff_possible", "accepted", "rejected", "unknown"]
    execution_id: str = Field(default="", pattern=r"^[A-Za-z0-9_.:-]{0,128}$")
    # Empty legacy bindings remain fenced; cleanup must not guess their route.
    provider_id: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9._-]{0,63})?$")
    operation: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9._-]{0,127})?$")
    managed_inputs: dict[Identifier, "InputReceipt"] = Field(default_factory=dict, max_length=64)
    cleanup: ProviderCleanupRecord = Field(default_factory=ProviderCleanupRecord)


class InputReceipt(Contract):
    input_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    source_asset_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}:[0-9]{3}$")]
    sha256: HexDigest
    mime_type: str = Field(max_length=128, pattern=r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
    size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)


class DelegationRecord(Contract):
    revision: Literal[1] = 1
    process: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    nonce_hash: HexDigest
    scope: DelegationScope
    state: Literal["prepared", "handoff_possible", "accepted", "rejected", "unknown"] = "prepared"
    admission: RuntimeAdmission | None = None
    closed: bool = False
    operations: dict[Path, OperationRecord] = Field(default_factory=dict, max_length=64)
    cleanup_window: CleanupWindow | None = None
    cleanup: RuntimeCleanupRecord = Field(default_factory=RuntimeCleanupRecord)

    @model_validator(mode="after")
    def retained_relation(self):
        approved = {x.path: x for x in self.scope.operations}
        if set(self.operations) - set(approved):
            raise ValueError("Unapproved retained operation")
        for path, operation in self.operations.items():
            if operation.input_digest != approved[path].input_digest:
                raise ValueError("Retained operation input changed")
            if operation.state == "accepted" and not operation.execution_id:
                raise ValueError("Accepted operation requires a provider identity")
            if bool(operation.provider_id) != bool(operation.operation):
                raise ValueError("Incomplete retained provider route")
            if operation.cleanup != ProviderCleanupRecord() and (
                not operation.execution_id
                or not operation.provider_id
                or self.cleanup_window is None
            ):
                raise ValueError("Provider cleanup requires the exact retained identity and window")
        if len({x.key for x in self.operations.values()}) != len(self.operations):
            raise ValueError("Dispatch key cannot name two occurrences")
        if len({x.provider_job_id for x in self.operations.values()}) != len(self.operations):
            raise ValueError("Provider staging identity cannot name two occurrences")
        if self.admission is not None:
            if (
                self.admission.target != self.scope.target
                or self.admission.request_digest != self.scope.runtime_request_digest
                or self.admission.plan_fingerprint != self.scope.runtime_plan_fingerprint
                or self.state != "accepted"
            ):
                raise ValueError("Retained Runtime admission changed")
        if self.state == "accepted" and self.admission is None:
            raise ValueError("Accepted delegation requires a Run identity")
        if self.operations and self.admission is None:
            raise ValueError("Provider operation requires an accepted Runtime relation")
        if self.cleanup != RuntimeCleanupRecord() and (
            self.admission is None or self.cleanup_window is None
        ):
            raise ValueError("Runtime cleanup requires the retained admission and window")
        for receipt in (
            self.cleanup.observation,
            self.cleanup.stop.receipt if self.cleanup.stop else None,
        ):
            if receipt is not None and receipt.admission != self.admission:
                raise ValueError("Cleanup Runtime relation changed")
        if self.cleanup_window is not None and not self.closed:
            raise ValueError("Cleanup cannot reopen useful dispatch")
        return self


@dataclass(frozen=True)
class DelegationHandle:
    job_id: str
    nonce: str = field(repr=False)


@dataclass(frozen=True)
class InternalProviderOperation:
    """Trusted registration, never an IR-authored purpose or dispatch authority.

    guard(scope, admission, occurrence, dispatch_key) must check current Runtime actor
    authority, exact pins/evidence, input access and enforceable host ownership.
    It remains held through provider acceptance. An unqualified embedding must not
    install this registration. The provider retains its normal staging guards.
    """

    capability: str
    fingerprint: str
    provider_id: str
    operation: str
    guard: Callable
    purpose: Literal["internal_provider"] = "internal_provider"
    # Separately admitted owned stop/read authority, never useful dispatch authority.
    cleanup_guard: Callable | None = None


def request_digest(request: GenerationRequest) -> str:
    return digest(
        "flamoris.runtime-provider-request.v1",
        {
            "workflow_id": request.workflow_id,
            "operation": request.operation,
            "recipe": request.payload.model_dump(mode="json"),
            "definition": request.definition.model_dump(mode="json")
            if request.definition
            else None,
            "runtime_evidence": request.runtime_evidence,
        },
    )


def recover_record(raw) -> DelegationRecord:
    # JSON decoding preserves strict tuple contracts without accepting Python coercion.
    return DelegationRecord.model_validate_json(json.dumps(raw, allow_nan=False))


def validate_root_request(scope, request):
    if request_digest(request) != scope.root_request_digest:
        raise ValueError("Delegation root request changed")
    recipe = request.payload
    if recipe.schema_version == 3 and any(
        getattr(recipe, field) != getattr(scope, field)
        for field in ("closure_digest", "structural_digest", "invocation_digest")
    ):
        raise ValueError("Generation media relation changed")
    if recipe.schema_version == 3 and recipe.definition_digest != scope.root_digest:
        raise ValueError("Generation root identity changed")


class RuntimeAdmissionRejected(ValueError):
    """Trusted port proof that no Runtime Run was admitted; never a transport error."""


class RuntimeDelegations:
    def __init__(
        self,
        jobs,
        registrations: tuple[InternalProviderOperation, ...],
        *,
        peer_check: Callable,
        clock=time.time,
    ):
        self.jobs = jobs
        self.clock = clock
        if not callable(peer_check):
            raise ValueError("A current authenticated peer check is required")
        self._peer_check = peer_check
        self._registrations = {}
        if len(registrations) > 128:
            raise ValueError("Internal operation registration limit exceeded")
        for registration in registrations:
            if (
                registration.purpose != "internal_provider"
                or not callable(registration.guard)
                or registration.capability in self._registrations
            ):
                raise ValueError("Only distinct guarded internal provider operations may register")
            self._registrations[registration.capability] = registration

    def _registration(self, occurrence):
        registration = self._registrations.get(occurrence.capability)
        if registration is None or registration.fingerprint != occurrence.fingerprint:
            raise ValueError("Internal operation pin is stale or unavailable")
        return registration

    def _request(self, occurrence):
        registration = self._registration(occurrence)
        request = self.jobs._validated_request(occurrence.workflow_id)
        if (request.operation, self.jobs._provider_route(request)) != (
            registration.operation,
            registration.provider_id,
        ) or request_digest(request) != occurrence.input_digest:
            raise ValueError("Delegated operation route or concrete input changed")
        return request, registration

    def _root(self, handle, principal, target):
        if self._peer_check(principal, target) is not True:
            raise ValueError("Runtime delegation peer authorization is unavailable")
        job = self.jobs._get(handle.job_id)
        record = job.delegation
        if (
            record is None
            or record.process != self.jobs._instance
            or handle.job_id in self.jobs._delegation_fenced
            or principal != record.scope.principal
            or target != record.scope.target
            or type(handle.nonce) is not str
            or len(handle.nonce) > 128
            or not hmac.compare_digest(
                record.nonce_hash, hashlib.sha256(handle.nonce.encode()).hexdigest()
            )
        ):
            raise ValueError("Runtime delegation is unavailable for this peer")
        if self.jobs._active_job_id != job.job_id:
            raise ValueError("Runtime delegation root is no longer active")
        self._closed(record)
        return job, record

    def _closed(self, record):
        if self.clock() >= record.scope.expires_at:
            self.jobs._delegation_expired.add(record.nonce_hash)
        return record.closed or record.nonce_hash in self.jobs._delegation_expired

    def _useful(self, record):
        if self._closed(record):
            raise ValueError("Runtime delegation is closed or expired")

    def _commit(self, job, record):
        # Never roll a possible handoff back into a reusable authorization.
        job.delegation = record
        try:
            self.jobs._persist_active(job)
        except BaseException:
            self.jobs._delegation_fenced.add(job.job_id)
            job.snapshot = JobSnapshot(status="unknown")
            raise

    async def prepare(self, workflow_id, scope: DelegationScope) -> DelegationHandle:
        if not isinstance(scope, DelegationScope):
            raise ValueError("A validated delegation scope is required")
        if self._peer_check(scope.principal, scope.target) is not True:
            raise ValueError("Runtime delegation peer authorization is unavailable")
        if not self.clock() < scope.expires_at <= self.clock() + 300:
            raise ValueError("Delegation expiry exceeds the bounded root profile")
        root = self.jobs._validated_request(workflow_id)
        validate_root_request(scope, root)
        for occurrence in scope.operations:
            self._request(occurrence)
        nonce = secrets.token_urlsafe(32)
        record = DelegationRecord(
            process=self.jobs._instance,
            nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
            scope=scope,
        )
        job = await self.jobs._reserve(workflow_id, delegation=record)
        return DelegationHandle(job.job_id, nonce)

    async def handoff(self, handle, principal, target, submit):
        if not callable(submit):
            raise ValueError("A trusted pinned Runtime admission port is required")
        job, _ = self._root(handle, principal, target)
        async with job.lock:
            job, record = self._root(handle, principal, target)
            if record.state != "prepared":
                return self.observation(handle, principal, target)
            self._useful(record)
            root = self.jobs._validated_request(job.workflow_id)
            validate_root_request(record.scope, root)
            for occurrence in record.scope.operations:
                self._request(occurrence)
            self._commit(job, record.model_copy(update={"state": "handoff_possible"}))
        try:
            remaining = record.scope.expires_at - self.clock()
            async with asyncio.timeout(min(60, max(0, remaining))):
                self._useful(job.delegation)
                admission = await submit(record.scope)
            if not isinstance(admission, RuntimeAdmission) or (
                admission.target != record.scope.target
                or admission.request_digest != record.scope.runtime_request_digest
                or admission.plan_fingerprint != record.scope.runtime_plan_fingerprint
            ):
                raise ValueError("Runtime admission receipt does not match the prepared plan")
        except BaseException as exc:
            async with job.lock:
                state = "rejected" if isinstance(exc, RuntimeAdmissionRejected) else "unknown"
                self._commit(job, job.delegation.model_copy(update={"state": state}))
                if state == "rejected":
                    status = "cancelled" if job.delegation.closed else "failed"
                    # This is owned no-admission cleanup, not a newly authorized peer command.
                    await self.jobs._release_delegation_unaccepted(job, status)
                else:
                    job.snapshot = JobSnapshot(status="unknown")
            raise
        async with job.lock:
            current = job.delegation.model_copy(
                update={"state": "accepted", "admission": admission}
            )
            self._commit(job, current)
            job.snapshot = JobSnapshot(status="cancel_requested" if current.closed else "queued")
        return self.observation(handle, principal, target)

    def observation(self, handle, principal, target):
        _, record = self._root(handle, principal, target)
        # No nonce/hash, private recipe, raw errors or filesystem locators.
        return {
            "job_id": handle.job_id,
            "state": record.state,
            "closed": self._closed(record),
            "admission": record.admission.model_dump(mode="json") if record.admission else None,
            "operations": {k: v.model_dump(mode="json") for k, v in record.operations.items()},
        }

    async def invoke(self, handle, principal, target, path, key, input_digest):
        job, _ = self._root(handle, principal, target)
        async with job.lock:
            job, record = self._root(handle, principal, target)
            occurrence = next((x for x in record.scope.operations if x.path == path), None)
            if occurrence is None or occurrence.input_digest != input_digest:
                raise ValueError("Unapproved occurrence or changed operation input")
            existing = record.operations.get(path)
            if existing is not None:
                if existing.key != key or existing.input_digest != input_digest:
                    raise ValueError("Delegated occurrence already has a different dispatch claim")
                return existing.model_dump(mode="json")
            if any(x.key == key for x in record.operations.values()):
                raise ValueError("Dispatch key already names a different occurrence")
            self._useful(record)
            if record.admission is None:
                raise ValueError("Internal operation requires the accepted Runtime Run relation")
            request, registration = self._request(occurrence)
            claim = OperationRecord(
                key=key,
                input_digest=input_digest,
                provider_job_id=uuid4().hex,
                state="handoff_possible",
                provider_id=registration.provider_id,
                operation=registration.operation,
            )
            operations = {**record.operations, path: claim}
            self._commit(job, record.model_copy(update={"operations": operations}))
        disposition = "unknown"
        updated = claim
        try:
            remaining = record.scope.expires_at - self.clock()
            # The mandatory registered guard stays held through provider staging/acceptance.
            with registration.guard(record.scope, record.admission, occurrence, key):
                async with asyncio.timeout(min(occurrence.timeout_ms / 1000, max(0, remaining))):
                    self._useful(job.delegation)
                    try:
                        receipt = await self.jobs.providers.get(registration.provider_id).submit(
                            request, claim.provider_job_id
                        )
                    except SubmissionRejected:
                        disposition = "rejected"
                        raise
                    if type(receipt.execution_id) is not str or not re.fullmatch(
                        r"[A-Za-z0-9_.:-]{1,128}", receipt.execution_id
                    ):
                        raise ValueError("Provider returned an invalid internal receipt")
                    # Preserve a known identity even if metadata validation/guard exit fails.
                    updated = claim.model_copy(update={"execution_id": receipt.execution_id})
                    updated = updated.model_copy(
                        update={
                            "state": "accepted",
                            "managed_inputs": {
                                name: InputReceipt.model_validate(value)
                                for name, value in receipt.managed_inputs.items()
                            },
                        }
                    )
                    updated = OperationRecord.model_validate(updated.model_dump())
        except BaseException:
            updated = updated.model_copy(update={"state": disposition})
            async with job.lock:
                self._commit(
                    job,
                    job.delegation.model_copy(
                        update={"operations": {**job.delegation.operations, path: updated}}
                    ),
                )
            raise
        async with job.lock:
            self._commit(
                job,
                job.delegation.model_copy(
                    update={"operations": {**job.delegation.operations, path: updated}}
                ),
            )
        self._root(handle, principal, target)  # Recheck data access after an in-flight response.
        return updated.model_dump(mode="json")

    async def release_unaccepted(self, handle, principal, target):
        job, _ = self._root(handle, principal, target)
        async with job.lock:
            job, _ = self._root(handle, principal, target)
            await self.jobs._release_delegation_unaccepted(job, "failed")
