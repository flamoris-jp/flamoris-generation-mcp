# Scoped Runtime delegation ownership

This source-level implementation follows
[Runtime's reviewed bridge contract](https://github.com/flamoris-jp/flamoris-ai-runtime/blob/main/docs/GENERATION_COMPOSITION_BRIDGE.md)
and Generation #53/#55. It provides private in-process seams in
`runtime_delegation.py` and `runtime_cleanup.py`;
no MCP tool, production composition profile, provider registration or deployment
configuration is added. Runtime #21/#22 supply the compiler/pinned-admission and
isolated-target counterparts. An authenticated transport must connect these seams
before service execution is available.

## One ordinary root reservation

`RuntimeDelegations.prepare` validates the existing captured workflow request and
approved occurrence requests, then calls the same `JobStore._reserve` used by normal
submission. It creates one public media Job, claims the existing single-active
reservation and writes the delegation inside `job-authority/active.json` before any
Runtime handoff. There is no second queue, physical resource ledger or public root
reservation for internal stages. Normal submission and verification still use the
same reservation helper.

The immutable scope retains the principal mapping, target Runtime incarnation,
media root/closure/structural/invocation/evidence identities, concrete Generation
request digest, expected Runtime request/plan fingerprints and approved occurrences.
Image v3 relations are checked against the captured recipe. Future media profiles
still require their owning domain adapter; this record does not turn arbitrary
digests into qualification or grants. Runtime's two uint64 incarnation counters and
Run counter remain decimal strings, preserving values above binary64 precision.

The scope permits at most 64 distinct occurrences and a root expiry within 300
seconds. Each operation has an explicit timeout of at most 60 seconds. These finite
baseline limits do not establish a longer Music execution profile or enlarge Runtime
limits. Its compiler and the registered guard still enforce aggregate effects,
resource/attempt/output limits and physical capacity.

Preparation mints a private transport nonce; only its hash is journaled. The nonce
is absent from handle repr, public metadata and observations. A public Job ID or
caller-authored principal alone cannot resolve a delegation. Nonce possession does
not authenticate a service: the embedding must authenticate the peer and supply the
fixed principal-to-target mapping upstream.

Construction requires a `peer_check` callback with no default. Preparation, handoff,
observation, duplicate/internal operation delivery and unaccepted-root release all
require its explicit `True` result for the current authenticated peer, including
expiry/revocation. Subject/target strings are not a replacement for this check.
Receipt recording after an in-flight response remains an owned internal obligation;
revocation does not discard it or grant another dispatch.

## Atomic handoff and internal operations

Before calling the trusted pinned Runtime admission port, `handoff` persists
`handoff_possible`. Concurrent/later duplicate delivery observes the existing
record without calling the port again. A matching receipt retains the target, Run
and expected request/plan fingerprints. Lost, malformed or mismatched responses
become unknown ownership and retain the root. `RuntimeAdmissionRejected` is reserved
for a trusted port's definite proof that no Run was admitted; transport errors must
not use it.

Only configured `InternalProviderOperation` registrations can execute; public-root
purpose registrations reject. The mandatory registered guard must check current
authenticated Runtime actor dispatch authority, exact capability/evidence pins,
input scopes and enforceable host ownership, and remain held through acceptance.
The guard receives the accepted Run relation as well as scope/occurrence/dispatch
key, so it must bind the actual actor grant to that Run rather than trust a key alone.
No default permissive guard or live registration is installed. Fixture guards do
not qualify a host or authenticate a real service.

An operation must match its approved occurrence, concrete request digest and
registered route/pin. Current workflow/domain/readiness validation runs again before
the claim. The existing provider retains its evidence continuity, before-POST,
managed-input staging and output-contract guards. The internal path calls the
provider adapter, never public `JobStore.submit`.

Each occurrence has one process-lifetime dispatch claim. One dispatch key cannot
name two occurrences. Same-key/same-input duplicates observe the retained receipt;
changed keys/inputs conflict. The possible-handoff claim is persisted before the
provider await. A private per-operation correlation ID separates staging/output
namespaces while the public root Job remains the original one. No retry is invented.

Acceptance retains the bounded provider execution ID and managed-input snapshots.
A new claim also retains its exact registered provider/operation route. Older
journals without that binding remain recoverable but cannot acquire cleanup
authority by guessing a route from replacement configuration.
A provider's definite `SubmissionRejected` consumes the claim without releasing an
already admitted Runtime root. Other exceptions, cancellation, timeout or journal
failure leave unknown/fenced ownership. A guard error after acceptance retains the
known provider ID for later read-only reconciliation; it cannot be classified as
no-send rejection. None of these outcomes permits replay.

## Cancellation, persistence and recovery

Normal `jobs.cancel` closes useful delegated dispatch. Before possible handoff it
can release the ordinary reservation. Afterwards it returns `cancel_requested`
and retains the root. This is a dispatch fence, not a Runtime/provider stop or
physical release receipt. Observed expiry is permanent in-process even if the wall
clock moves backwards. Same-key observation remains read-only after closing.

Journal errors after preparation fence the in-memory root. Recovery validates the
bounded relation and fences its old process incarnation. Public status/result stay
metadata-only/unknown: no automatic root-provider poll, restored Run, media success,
old-root release or replay. An old nonce cannot reactivate the recovered delegation.
Malformed records fail closed.

## Bounded owned stop and observation

`RuntimeOwnedCleanup` is a private embedding obligation. Normal `jobs.cancel`
persists the useful-dispatch fence; the owning embedding can then explicitly call
the cleanup seam. No server registration, automatic cleanup task, MCP tool or live
transport is installed. Each method requires the existing active root and a closed,
expired or fenced useful delegation. Cleanup never invokes workflow validation,
provider submit, result download/publication or resource release.

Useful peer authorization and owned cleanup authority are separate. An explicit
`InternalProviderOperation.cleanup_guard(scope, admission, occurrence, operation)`
must authenticate the owning service and check the original retained operation,
input/host obligations and separately admitted stop/read permission. There is no
default guard; expired/revoked caller access does not grant cleanup permission.
Configured owned obligations may survive that revocation, while caller reads and
duplicate useful deliveries still require the current `peer_check`.

The optional `RuntimeCleanupPort` fixes the original Runtime target and requires a
separate guard for the exact accepted Run/request/plan relation. Its callbacks
project the existing Runtime `CommandReceipt` and `RunSnapshot` contracts. The
embedding supplies actual authenticated command/status access; these projections
do not bypass Runtime authorization or qualify a transport. A new Runtime
incarnation cannot receive an old Run counter. Unknown admissions/execution IDs,
changed capability pins/routes and absent owned provider methods reject without
guessing an ID or enabling useful dispatch.

Cleanup has one persisted 30-second window starting at its first admitted owned
operation. Each await is limited to five seconds and the remaining window. The
Runtime and each of at most 64 approved provider occurrences have one stop claim
and at most four inspections. Claims/counts are journaled before any await. These
limits survive restart; an observed expiry or clock rollback before the window's
start permanently closes the window. Expiry/exhaustion retains unresolved debt.
Receipt recording after an in-flight response remains an owned obligation.

Concurrent/late duplicate stop delivery observes the retained claim. A lost,
malformed, timed-out or cancelled response consumes that claim without retry;
changing targeted-stop configuration later cannot send it again. A known valid
receipt is retained even if guard teardown or journal publication fails. Inspection
is read-only and serialized under the root lock. Failed GETs consume their budget.
Runtime observations preserve uint64 decimal watermark strings, reject regression
and changed data at the same watermark, and preserve terminal lifecycle state.
Provider terminal observations likewise cannot be overwritten by later conflicting
or non-terminal snapshots. No event replay can invoke these methods.

ComfyUI supplies status-only `inspect_owned`/`cancel_owned` methods. They use the
same scoped HTTP client and targeted-interrupt configuration as normal jobs, but
do not restore output-node maps, output contracts or publication authority after
restart. Queue deletion still names only the original prompt. Absence after deletion
is `unknown`: ComfyUI does not acknowledge which queued prompt was deleted, and a
dispatch/history race can produce the same observation. Only terminal history
confirms cancellation/completion/failure; unsupported running cancellation never
falls back to a global interrupt. This stricter absence rule also applies to normal
jobs and deadline cancellation.

An admitted delegation intentionally cannot reach successful public finalization
in this slice. A stop receipt or terminal observation does not settle physical
host/input/provider debt or release the Generation reservation. After the bounded
cleanup window, unresolved ownership remains in the journal for a separately
reviewed settlement/reconciliation policy; no new window is automatically granted.
Authenticated transport, current host acquisition/release, complete validated
output publication and provider/input/root settlement receipts belong to the next
bridge slice. There is
no boolean force-settled shortcut. Runtime #19 remains open; live host/provider and
two-user qualification are required before shared Studio use.

## Executable evidence

`tests/test_runtime_delegation.py` exercises the real JobStore and ComfyUI HTTP
adapter with explicit offline Runtime/guard fixtures: journal-before-handoff,
normal-root exclusivity, peer/nonce mismatch, duplicates, input/key conflicts,
distinct staging identities, unknown Runtime/provider responses, task cancellation,
definite rejection, guard teardown, expiry, counter bounds and restart fencing.

`tests/test_image_v3_execution.py` checks that the internal path preserves actual
Image v3 revocation-before-POST and validates Generation media identities. These
cover portions of B02/B04/B05/B09/B12/B13; they do not qualify a real transport,
physical host or full bridge acceptance.

`tests/test_runtime_cleanup.py` additionally checks journal-before-stop/read,
concurrent duplicates, late accepted Runtime/provider identities, revocation,
scoped/unsupported interrupts, unknown responses, finite budgets/windows, restart
fencing without publication, changed original relations, journal failures and
immutable terminal observations. Terminal lifecycle/command receipts alone retain
the root in every case. These are offline portions of B09/B10/B12/B13, not complete
host settlement or deployed two-user acceptance.
