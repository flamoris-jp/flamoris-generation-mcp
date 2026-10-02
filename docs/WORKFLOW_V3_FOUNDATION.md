# Workflow v3 static foundation

Implemented scope: the first internal delivery of #45. The public MCP contracts,
saved recipes, v1/v2 definitions, Image verification, JobStore and provider
execution are unchanged. This delivery does **not** register production Music,
Speech or Video profiles or advertise compositions as ready.

## Internal contracts

`workflow_v3.Definition` discriminates explicit provider artifacts from static
compositions. Inputs/outputs have bounded types, format/schema revisions,
semantic roles, MIME domains and cardinality. Providers carry an explicit adapter
revision; a configured validator must approve the **entire** profile/ports/artifact
contract. No default validator assumes that a syntactically valid graph is safe.

Portable artifacts and JSON values are bounded before recursive traversal and
canonicalization. Raw JSON ingress rejects duplicate keys and nonfinite numbers.
Definition, media-plan and invocation digests use distinct versioned domains.
Scalar numeric inputs normalize to binary64 for `number`; `integer` rejects bool.
Ordered arrays are semantic; effect sets normalize to sorted order.

`definition_versions.DefinitionVersions` publishes one atomic bounded index under
`definition-versions-v3/index.json`. It retains exact immutable id/version/digest
records independently of active aliases. Updating an alias preserves a child's
old pin. Revocation rejects affected closures, including after restart. Returned
definitions are detached copies. Publication rejects capacity overflow, and an
uncertain durable write fences the live store until restart/reconciliation.
The baseline remains one process-owned writer; it is not a distributed registry.

There are at most 256 retained records, 126 active aliases and 8 MiB of encoded
index storage, with existing stricter JSON bounds also applying. No GC is exposed:
all retained versions are conservatively pinned. Later reference-aware collection
must cover active parents, recipes, plans, attestations and in-flight work before
any deletion can be permitted.

`composition.Compiler` resolves exact includes into occurrence-qualified provider
steps. It validates public binding direction, type/schema/role, source-domain
containment, cardinality and defaults; orders dependencies deterministically; and
rejects disconnected hidden executions, duplicate sources and cycles. Limits are
eight include edges per path, 64 occurrences, 1024 expanded bindings, 256 total
declared steps and 2 MiB of expanded definition/manifest content. Every nested
composition checks aggregate child ceilings, including intermediate outputs.
The compiler deliberately treats scalar/structured ports as single-value slots;
asset collections have bounded cardinality and require a managed-input validator.

`Plan.bind` validates declared scalar slots and separates structural identity from
the concrete invocation hash. It does not authorize an asset handle or implement
ABC/JSON format semantics: these require the registered profile validator.

## Remaining execution gates

This code is an internal static compiler, not another scheduler. A manifest is
not an authorization grant or readiness attestation. It cannot execute providers,
stage assets, choose a healthy implementation, resume work or activate a runtime.

Next #45 deliveries must connect a version-negotiated catalog and saved-recipe
contract to reviewed adapters; implement one-provider artifact lowering and whole
composition automatic smoke/attestation through the existing JobStore; validate
concrete intermediate content/ownership immediately before handoff; and extend
media output role metadata. Cross-provider work additionally requires Runtime #19.
No exact production profile or provider API is inferred from these test fixtures.

## Verification

Run the normal repository checks, including:

```bash
python -m pytest tests/test_composition.py
python -m pytest
ruff check .
ruff format --check .
python -m build
```

The focused tests use a bounded fixture adapter and exercise history/restart,
revocation, failed/uncertain publication, symlink rejection, include occurrences,
cycles, typed bindings, whole-plan limits, canonical parsing and invocation pins.
They require no GPU, models, production provider or private host.
