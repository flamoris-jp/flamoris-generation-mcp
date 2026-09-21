# AGENTS.md

## Scope

These instructions apply to the entire repository.

## Project direction

- Keep this project small, practical, and MCP-native.
- Implement the server in Python.
- Treat ComfyUI as the first generation provider, not as the identity of the project.
- Keep room for future image, video, music, and voice providers without implementing speculative abstractions early.
- Prefer incremental phases that can be exercised with real models quickly.

## Architecture

- Keep provider-specific HTTP/API behavior behind small adapter/client modules.
- Keep MCP tools provider-neutral where practical.
- Preserve one clear execution authority: providers execute jobs; this server validates, builds, submits, observes, and returns results.
- Prefer trusted workflow templates plus validated user-facing parameters over arbitrary raw node mutation.
- Do not expose arbitrary ComfyUI graph-editing tools unless a future issue explicitly requires them.
- Keep model discovery filesystem-based unless a future requirement justifies a registry or database.
- LoRA support must preserve explicit ordering and separate model/CLIP strengths.

## Compatibility and dependencies

- Do not depend on the .NET implementation or packages from `flamoris-mcp-core`.
- `flamoris-mcp-core` may be used only as a design reference for MCP boundaries and behavior.
- Avoid unnecessary frameworks or infrastructure in early phases.
- Keep dependency ranges bounded and intentional.

## Configuration and documentation

- Environment-specific values must be configurable through environment variables or equivalent runtime configuration.
- Do not commit or document personal machine names, usernames, private hostnames, private network topology, secrets, API keys, or developer-specific absolute paths.
- Public documentation and examples must remain environment-neutral.
- Do not commit model weights, generated media, local configuration, or credentials.

## Testing and CI

- Normal CI must not require a live GPU, live ComfyUI instance, installed model weights, or other local generation services.
- Mock provider HTTP behavior in normal tests.
- Add focused tests for tool validation, workflow construction, model discovery, provider response handling, and error paths.
- Keep CI fast enough for normal pull-request iteration.
- Preserve PR-level concurrency with stale runs cancelled when applicable.
- Before completing a change, run the relevant tests, lint/format checks, package/build smoke, and installed-package import smoke when packaging is affected.

## Safety and robustness

- Treat provider responses and filesystem paths as untrusted input.
- Reject path traversal, ambiguous model identities, malformed provider responses, and unsafe raw workflow injection.
- Do not automatically retry non-idempotent generation submissions after ambiguous failures.
- Keep returned provider errors bounded and actionable; do not expose unnecessary tracebacks or private provider details.
- Prefer scoped cancellation. Do not fall back to a global provider interrupt when a targeted operation is unavailable.

## Change discipline

- Follow the current issue as the source of truth for scope.
- Do not expand a phase with unrelated UI, deployment, authentication, database, download-manager, or provider work unless the issue explicitly requires it.
- Keep changes reviewable and avoid unrelated refactors.
- Update README or other public documentation when behavior, configuration, or supported tools change.
