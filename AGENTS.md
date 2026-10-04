# AGENTS.md

These instructions apply to the repository. Read README.md and [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) / [Generation #67](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/67) before changing generation integration.

## Current authorization and target

Only documentation review/fixes and explicitly requested documentation merges are in scope now. Do not implement Controller, delegate Generation work to Work, delete/migrate code or data, expand ComfyWorkFlow/reference-image features, change catalogs, deploy, switch runtimes or call providers. Intelligence cleanup elsewhere has priority.

Generation MCP's target responsibility is the external MCP facade used by ChatGPT through Hub. Internal FLAMORIS service/application calls use non-MCP contracts. The current Python package co-locates generation-domain code; document this as-built fact without making it permanent ownership or pretending it was already removed.

The existing MCP-side ComfyWorkFlow subsystem is for later removal, not transfer into Controller or automatic recreation. Inventory precise source, tests, public tools, Studio callers, retained providers and data before a separately authorized deletion. Do not delete a generic recipe store merely because its name contains workflow; it may support non-ComfyUI providers too.

Controller remains documentation-only. Old Generation Hub expansion or Runtime-bridge plans are historical/held, not implementation instructions overriding #18.

## Terminology and identifiers

Use ComfyWorkFlow for ComfyUI graph/API-format JSON. Use ExecuteFlow for Runtime inference flow and keep the existing compiled ExecutionPlan distinct. Non-ComfyUI generation requests/recipes are not automatically ComfyWorkFlow. Avoid bare Workflow as new FLAMORIS architecture prose.

Preserve exact current `workflows.*`, `workflow_id`, `WorkflowDefinition`, `WorkflowStore`, schemas, configuration keys and file paths until an explicit compatibility-reviewed implementation change. `workflow_id` returned by build is a built recipe handle, not necessarily the registered definition ID. `FLAMORIS_WORKFLOW_DIR` stores recipes; `FLAMORIS_WORKFLOW_DEFINITION_DIR` stores registered definitions. Do not invent a config or API rename in docs.

## Retained behavior and authority

Providers execute; current generation code validates/builds/submits/observes and materializes bounded outputs. Keep provider APIs behind adapters, explicit provider selection, trusted ComfyUI definitions and declared parameters, model/catalog checks and ordered LoRA semantics where supported. Do not expose arbitrary raw graph mutation as a shortcut.

MCP protocol validation/annotations/content mapping belongs to the external facade. Generation jobs, recipes, references and assets have one current authority until a reviewed change. Do not instantiate competing stores/reservations per frontend or equate process locks with host-wide GPU control. GPU Node Manager alone owns configured host lifecycle. Agent is optional personality, products retain document/user authority and Commons owns generic infrastructure.

## Safety and cleanup boundaries

Source deletion is not deletion of saved definitions, assets, inputs, historical evidence, credentials or unresolved provider work. Retained paths preserve authorization, immutable references, decoding/staging/transfer limits, confinement, metadata/provenance and safe errors. Unsupported calls after eventual retirement must fail explicitly, not bypass checks, fabricate success or fall back to a removed path.

Preserve singleton/drain/restart semantics, durable unknown reservations and no replay after ambiguous submission. A missing queue record does not prove provider work stopped. Prefer scoped cancellation; do not invoke a global interrupt when a targeted operation is unavailable.

Static JSON validity, provider availability, infrastructure readiness and generation qualification are distinct. Preserve protections required by retained functionality; retirement of unused feature-only checks is reviewed explicitly, not a blanket requirement to retain every obsolete subsystem forever or to bypass all safety gates.

Provider responses/paths and user-supplied metadata are untrusted. No arbitrary URLs, filesystem paths, shell, hidden model downloads, automatic provider fallback or implicit GPU switching. Configuration remains operator-controlled and environment-neutral. Do not publish private topology, hostnames, user paths, secrets, weights or generated private media.

## Tests and review

The actual issue defines scope. Do not add unrelated UI/auth/database/provider/deployment work. Read exact current source and tests before later cleanup and preserve/version external compatibility deliberately. Normal CI uses fake provider HTTP and bounded fixtures without live GPUs, weights or model services. Validate retained tool mapping, recipe building/identity, model discovery, state, provider errors, cancellation and negative paths. Run lint/format/package smoke when applicable; keep existing PR concurrency behavior.

Review final prose and literal identifiers, not only keyword matches. Explicit user approval is required for merge; documentation merge does not start implementation. Do not claim unrun tests, provider support or live qualification.

## License and support

Code/docs are Apache-2.0 unless otherwise stated. Third-party source, models, weights, data, fonts and media require compatible documented terms; provider/generated assets do not automatically inherit the code license. FLAMORIS has no guaranteed individual support; documentation, Issues, tests, logs and source are primary references.
