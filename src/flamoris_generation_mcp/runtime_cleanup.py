"""Bounded owned cleanup, separate from useful dispatch and caller data access.

No transport or live registration is installed. These methods are private trusted
embedding obligations, not caller-authored commands or resource-release evidence.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from .providers.base import JobSnapshot
from .runtime_delegation import (
    CleanupWindow,
    DelegationRecord,
    InternalProviderOperation,
    ProviderObservation,
    ProviderStopRecord,
    RuntimeObservation,
    RuntimeStopReceipt,
    RuntimeStopRecord,
    RuntimeTarget,
)

PROVIDER_TERMINAL = {"completed", "failed", "cancelled"}
RUNTIME_TERMINAL = {"succeeded", "failed", "cancelled"}
PER_CALL_SECONDS = 5


@dataclass(frozen=True)
class RuntimeCleanupPort:
    """Fixed original target, with separately admitted owned control/read authority.

    guard(scope, admission) authenticates the configured owning service and exact
    original Run. cancel/inspect project the existing Runtime command/status port.
    A replacement Runtime incarnation cannot reconcile an old Run counter.
    """

    target: RuntimeTarget
    guard: Callable
    cancel: Callable
    inspect: Callable


class _JournalFailure(ValueError):
    pass


class _ObservationConflict(ValueError):
    pass


class _CleanupWindowClosed(ValueError):
    pass


class _InspectionBudgetExhausted(ValueError):
    pass


class RuntimeOwnedCleanup:
    def __init__(self, jobs, registrations, *, runtime_port=None, clock=time.time):
        self.jobs = jobs
        self.clock = clock
        self.runtime_port = runtime_port
        if runtime_port is not None and (
            not isinstance(runtime_port, RuntimeCleanupPort)
            or not isinstance(runtime_port.target, RuntimeTarget)
            or not all(
                callable(x) for x in (runtime_port.guard, runtime_port.cancel, runtime_port.inspect)
            )
        ):
            raise ValueError("A guarded fixed-target Runtime cleanup port is required")
        self.registrations = {}
        if len(registrations) > 128:
            raise ValueError("Owned cleanup registration limit exceeded")
        for registration in registrations:
            if (
                not isinstance(registration, InternalProviderOperation)
                or registration.purpose != "internal_provider"
                or registration.capability in self.registrations
                or (
                    registration.cleanup_guard is not None
                    and not callable(registration.cleanup_guard)
                )
            ):
                raise ValueError("Owned cleanup requires distinct explicit internal registrations")
            self.registrations[registration.capability] = registration

    def _owned(self, job_id):
        job = self.jobs._get(job_id)
        record = job.delegation
        if not isinstance(record, DelegationRecord) or self.jobs._active_job_id != job_id:
            raise ValueError("Owned cleanup requires the existing active delegated reservation")
        if not (
            record.closed
            or job_id in self.jobs._delegation_fenced
            or record.nonce_hash in self.jobs._delegation_expired
            or self.clock() >= record.scope.expires_at
        ):
            raise ValueError("Useful delegation must be closed before owned cleanup")
        return job, record

    def _window(self, job, record):
        now = self.clock()
        window = record.cleanup_window
        if window is None:
            window = CleanupWindow(started_at=now, expires_at=now + 30)
        if window.closed or not window.started_at <= now < window.expires_at:
            self._write(
                job,
                record.model_copy(
                    update={
                        "closed": True,
                        "cleanup_window": window.model_copy(update={"closed": True}),
                    }
                ),
            )
            raise _CleanupWindowClosed(
                "Owned cleanup window is closed; unresolved debt remains owned"
            )
        return record.model_copy(update={"closed": True, "cleanup_window": window})

    def _write(self, job, record):
        job.delegation = DelegationRecord.model_validate(record.model_dump())
        try:
            self.jobs._persist_active(job)
        except BaseException:
            self.jobs._delegation_fenced.add(job.job_id)
            job.snapshot = JobSnapshot(status="unknown")
            raise _JournalFailure("Owned cleanup journal outcome is unknown") from None
        if job.snapshot.status != "unknown":
            job.snapshot = JobSnapshot(status="cancel_requested")

    @staticmethod
    def _component(record, path):
        return record.cleanup if path is None else record.operations[path].cleanup

    @staticmethod
    def _update(record, path, component):
        if path is None:
            return record.model_copy(update={"cleanup": component})
        operation = record.operations[path].model_copy(update={"cleanup": component})
        return record.model_copy(update={"operations": {**record.operations, path: operation}})

    def _context(self, record, path, *, stop):
        if record.admission is None:
            raise ValueError("Owned cleanup cannot guess an unknown Runtime admission")
        if path is None:
            port = self.runtime_port
            if port is None or port.target != record.scope.target:
                raise ValueError("Original Runtime cleanup target is unavailable")
            call = port.cancel if stop else port.inspect
            return (
                lambda: port.guard(record.scope, record.admission),
                lambda: call(record.scope, record.admission),
            )
        occurrence = next((x for x in record.scope.operations if x.path == path), None)
        operation = record.operations.get(path)
        registration = self.registrations.get(occurrence.capability) if occurrence else None
        if (
            operation is None
            or not operation.execution_id
            or registration is None
            or registration.fingerprint != occurrence.fingerprint
            or not callable(registration.cleanup_guard)
            or not operation.provider_id
            or (operation.provider_id, operation.operation)
            != (registration.provider_id, registration.operation)
        ):
            raise ValueError("Exact owned provider route or cleanup authority is unavailable")
        provider = self.jobs.providers.get(operation.provider_id)
        call = getattr(provider, "cancel_owned" if stop else "inspect_owned", None)
        if not callable(call):
            raise ValueError("Provider has no registered owned cleanup operation")
        return (
            lambda: registration.cleanup_guard(
                record.scope, record.admission, occurrence, operation
            ),
            lambda: call(operation.execution_id),
        )

    @staticmethod
    def _receipt(raw, record, path, *, stop):
        if path is not None:
            if not isinstance(raw, JobSnapshot):
                raise ValueError("Invalid owned provider observation")
            return ProviderObservation(
                status=raw.status, cancel_supported=raw.metadata.get("cancel_supported")
            )
        contract = RuntimeStopReceipt if stop else RuntimeObservation
        if not isinstance(raw, contract):
            raise ValueError("Invalid owned Runtime observation")
        receipt = contract.model_validate(raw.model_dump())
        if receipt.admission != record.admission:
            raise ValueError("Owned Runtime relation changed")
        return receipt

    @staticmethod
    def _terminal(component, path):
        observation = component.observation
        if path is None:
            return observation is not None and observation.state in RUNTIME_TERMINAL
        return (observation is not None and observation.status in PROVIDER_TERMINAL) or (
            component.stop is not None
            and component.stop.receipt is not None
            and component.stop.receipt.status in PROVIDER_TERMINAL
        )

    @staticmethod
    def _merge_observation(component, receipt, path):
        previous = component.observation
        if (
            path is None
            and component.stop is not None
            and component.stop.receipt is not None
            and component.stop.receipt.terminal
            and receipt.state not in RUNTIME_TERMINAL
        ):
            raise _ObservationConflict()
        if path is None and previous is not None:
            if (
                int(receipt.watermark) < int(previous.watermark)
                or (receipt.watermark == previous.watermark and receipt != previous)
                or (previous.state in RUNTIME_TERMINAL and receipt.state != previous.state)
            ):
                raise _ObservationConflict()
        if path is not None:
            terminal = previous if previous and previous.status in PROVIDER_TERMINAL else None
            if terminal is None and component.stop is not None:
                terminal = component.stop.receipt
            if (
                terminal is not None
                and terminal.status in PROVIDER_TERMINAL
                and receipt.status != terminal.status
            ):
                raise _ObservationConflict()
        return component.model_copy(update={"observation": receipt, "last_failure": None})

    async def _stop(self, job_id, path):
        job, record = self._owned(job_id)
        guard, call = self._context(record, path, stop=True)
        claimed, receipt = False, None
        contract = RuntimeStopRecord if path is None else ProviderStopRecord
        try:
            with guard():
                async with job.lock:
                    _, record = self._owned(job_id)
                    component = self._component(record, path)
                    if component.stop is not None:
                        return component.stop.model_dump(mode="json")
                    if self._terminal(component, path):
                        return {"state": "terminal_observed"}
                    record = self._window(job, record)
                    claim = contract(state="handoff_possible")
                    self._write(
                        job,
                        self._update(record, path, component.model_copy(update={"stop": claim})),
                    )
                    claimed = True
                remaining = job.delegation.cleanup_window.expires_at - self.clock()
                async with asyncio.timeout(min(PER_CALL_SECONDS, max(0, remaining))):
                    raw = await call()
                receipt = self._receipt(raw, record, path, stop=True)
        except BaseException as exc:
            if isinstance(exc, _JournalFailure):
                raise
            if claimed:
                async with job.lock:
                    component = self._component(job.delegation, path)
                    unknown = contract(state="unknown", receipt=receipt)
                    self._write(
                        job,
                        self._update(
                            job.delegation, path, component.model_copy(update={"stop": unknown})
                        ),
                    )
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if not claimed and isinstance(exc, _CleanupWindowClosed):
                raise
            raise ValueError(
                "Owned stop outcome is unknown"
                if claimed
                else "Owned cleanup authority is unavailable"
            ) from None
        async with job.lock:
            component = self._component(job.delegation, path)
            observed = contract(state="observed", receipt=receipt)
            self._write(
                job,
                self._update(job.delegation, path, component.model_copy(update={"stop": observed})),
            )
        return observed.model_dump(mode="json")

    async def _inspect(self, job_id, path):
        job, record = self._owned(job_id)
        guard, call = self._context(record, path, stop=False)
        claimed = False
        try:
            with guard():
                # Serialize read results and journal the finite budget before GET.
                async with job.lock:
                    _, record = self._owned(job_id)
                    component = self._component(record, path)
                    if component.inspections >= 4:
                        raise _InspectionBudgetExhausted("Owned inspection budget exhausted")
                    record = self._window(job, record)
                    pending = component.model_copy(
                        update={"inspections": component.inspections + 1}
                    )
                    self._write(job, self._update(record, path, pending))
                    claimed = True
                    remaining = record.cleanup_window.expires_at - self.clock()
                    async with asyncio.timeout(min(PER_CALL_SECONDS, max(0, remaining))):
                        raw = await call()
                    receipt = self._receipt(raw, record, path, stop=False)
                    updated = self._merge_observation(pending, receipt, path)
                    self._write(job, self._update(job.delegation, path, updated))
        except BaseException as exc:
            if isinstance(exc, _JournalFailure):
                raise
            if claimed:
                async with job.lock:
                    component = self._component(job.delegation, path)
                    failed = component.model_copy(
                        update={
                            "last_failure": "observation_conflict"
                            if isinstance(exc, _ObservationConflict)
                            else "unknown"
                        }
                    )
                    self._write(job, self._update(job.delegation, path, failed))
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if not claimed:
                # Only local bounded errors, never arbitrary guard details.
                if isinstance(exc, (_InspectionBudgetExhausted, _CleanupWindowClosed)):
                    raise
                raise ValueError("Owned cleanup authority is unavailable") from None
            raise ValueError(
                "Owned observation conflicts with retained evidence"
                if isinstance(exc, _ObservationConflict)
                else "Owned inspection outcome is unknown"
            ) from None
        return receipt.model_dump(mode="json")

    async def stop_runtime_owned(self, job_id):
        return await self._stop(job_id, None)

    async def stop_provider_owned(self, job_id, path):
        if path is None:
            raise ValueError("An exact owned provider occurrence is required")
        return await self._stop(job_id, path)

    async def inspect_runtime_owned(self, job_id):
        return await self._inspect(job_id, None)

    async def inspect_provider_owned(self, job_id, path):
        if path is None:
            raise ValueError("An exact owned provider occurrence is required")
        return await self._inspect(job_id, path)
