# FLAMORIS Generation Hub Design

Updated: 2026-09-22

## 1. Purpose

Expand `flamoris-generation-mcp` from a ComfyUI-oriented image generation MCP server into a local Generation Hub that can coordinate image, video, music, speech, and analysis runtimes.

The Hub does not implement generation algorithms itself.

It coordinates:

- capability discovery
- workflow/request validation
- provider selection
- generation resource coordination
- job lifecycle
- cancellation
- result normalization
- asset management
- MCP API exposure

Core principle:

> Provider executes. Generation Hub coordinates.

## 2. Target runtimes

Initial target set:

| Runtime | Role | Provider |
|---|---|---|
| JANKU | text-to-image | ComfyUI |
| AnimeGen | image-to-video / motion reference | ComfyUI |
| SeeThrough | image decomposition / PSD | ComfyUI |
| YuE2 | music planning / music generation | YuE2 |
| SheetSage2 | audio transcription / music analysis | SheetSage2 |
| Irodori-TTS | text-to-speech | Irodori |

AI model/runtime identity and provider identity must remain separate.

For example:

```text
Provider
└── ComfyUI
    ├── JANKU
    ├── AnimeGen
    └── SeeThrough
```

JANKU, AnimeGen, and SeeThrough are different capabilities/workflows executed through the same ComfyUI provider.

## 3. Architecture

```text
                    ChatGPT / FLAMORIS apps
                              │
                              │ MCP
                              ▼
                  ┌─────────────────────┐
                  │  Generation Hub     │
                  │                     │
                  │ Capability Registry │
                  │ Workflow Store      │
                  │ Job Store           │
                  │ Resource Manager    │
                  │ Asset Store         │
                  └─────────┬───────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
         ComfyUI         YuE2         Irodori
              │
        ┌─────┼─────┐
        ▼     ▼     ▼
      JANKU AnimeGen SeeThrough

                            │
                            ▼
                       SheetSage2
```

Generation and analysis runtimes use the same Job abstraction even when their execution model differs.

## 4. Provider boundary

The current `JobStore` directly depends on `ComfyUIClient`. The Hub should introduce a provider-neutral execution boundary.

Conceptual interface:

```python
class GenerationProvider(Protocol):
    async def health(self) -> ProviderHealth: ...

    async def submit(
        self,
        operation: Operation,
        request: GenerationRequest,
        job_id: str,
    ) -> ProviderJob: ...

    async def inspect(
        self,
        provider_job_id: str,
    ) -> JobSnapshot: ...

    async def cancel(
        self,
        provider_job_id: str,
    ) -> JobSnapshot: ...

    async def materialize(
        self,
        output: ProviderOutput,
    ) -> bytes | Path: ...

    async def close(self) -> None: ...
```

Expected provider modules:

```text
providers/
├── base.py
├── comfyui.py
├── yue2.py
├── sheetsage2.py
└── irodori.py
```

The Hub core must not know ComfyUI HTTP routes or provider-specific response shapes.

## 5. Capability registry

The Hub should expose what it can do separately from how that capability is implemented.

Candidate capability IDs:

```text
image.generate
image.decompose
video.generate
video.motion_reference
music.plan
music.generate
music.transcribe
speech.generate
```

Candidate MCP tools:

```text
capabilities.list
capabilities.get
```

Example response:

```json
{
  "capabilities": [
    {
      "id": "image.generate",
      "provider": "comfyui",
      "runtime": "janku",
      "available": true
    },
    {
      "id": "music.generate",
      "provider": "yue2",
      "runtime": "yue2",
      "available": true
    }
  ]
}
```

Clients should request capabilities such as `music.generate`, not depend directly on a particular provider protocol.

## 6. Workflow / recipe model

The existing workflow model begins with:

```text
text-to-image
text-to-image-lora
```

The long-term workflow set may include:

```text
image.text-to-image
image.text-to-image-lora
image.decompose

video.image-to-video
video.motion-reference

music.plan
music.generate
music.transcribe

speech.generate
```

A future recipe should describe the requested operation and runtime without embedding provider-specific transport details.

Conceptual schema:

```json
{
  "schema_version": 2,
  "operation": "music.generate",
  "runtime": "yue2",
  "parameters": {}
}
```

Automatic best-provider selection is not required initially.

## 7. Job authority

`JobStore` remains the process-owned job/session authority.

The Job model should become provider-neutral:

```text
Job
├── job_id
├── operation
├── provider_id
├── runtime_id
├── provider_job_id
├── request
├── status
├── error
└── outputs
```

Common states:

```text
submitting
queued
running
completed
failed
cancel_requested
cancelled
unknown
```

Provider-specific states are normalized inside provider adapters.

## 8. Resource coordination

The existing single-generation-at-a-time guard is intentionally retained.

Initial capacity:

```text
active generation/analysis jobs = 1
```

Examples:

```text
JANKU active
→ YuE2 submit = BUSY

YuE2 active
→ AnimeGen submit = BUSY

Irodori active
→ JANKU submit = BUSY
```

This is a Hub-level resource guard, not a ComfyUI-specific lock.

A later phase may evolve the guard into resource leases such as:

```text
gpu:lime
cpu:analysis
```

Example future mapping:

```text
AnimeGen
  requires: gpu:lime

YuE2
  requires: gpu:lime

Irodori
  requires: gpu:lime

SheetSage2
  requires: cpu:analysis
```

Configurable parallel execution is not required in the initial Hub foundation.

## 9. Runtime lifecycle

Some runtimes compete for the same GPU memory.

A future `RuntimeManager` may coordinate lifecycle:

```text
ensure_runtime("yue2")
    ↓
stop conflicting runtime
    ↓
release GPU memory
    ↓
start yue2
    ↓
health check
    ↓
execute
```

However, the first Hub phases must not control systemd/sudo or automatically stop/start runtimes.

Initially, runtime lifecycle is observed through health/availability only.

## 10. Asset abstraction

Assets must expand beyond image files.

Provider-neutral asset metadata:

```text
Asset
├── asset_id
├── job_id
├── kind
├── mime_type
├── filename
├── size_bytes
├── materialized
└── metadata
```

Candidate kinds:

```text
image
video
audio
midi
score
archive
document
metadata
```

Examples:

YuE2:

```text
audio.wav
score.abc
```

SheetSage2:

```text
transcription.mid
melody.mid
chords.mid
score.abc
events.json
```

Irodori:

```text
audio.wav
```

SeeThrough:

```text
layers.psd
preview.png
layers.json
parts/*.png
```

AnimeGen:

```text
video.mp4
frames/*
```

`assets.list` remains metadata-only.

## 11. Large asset retrieval

The current inline retrieval bound is appropriate for small image assets but may be insufficient for large video, audio, or PSD outputs.

A later asset model may distinguish:

```text
retrieval:
  inline
  local_file
```

Small assets such as PNG, JSON, ABC, and MIDI may remain suitable for `assets.get`.

Large video/audio/PSD outputs should prefer Hub-owned local materialization.

Do not add an HTTP file server or object storage in the initial Hub foundation.

## 12. Provider implementations

### ComfyUIProvider

Initially owns the existing ComfyUI execution behavior.

Expected future runtime/workflow families:

```text
JANKU
AnimeGen
SeeThrough
```

Existing ComfyUI HTTP behavior should move behind the provider boundary without changing external behavior.

### YuE2Provider

Future capabilities:

```text
music.plan
music.generate
```

Primary outputs:

```text
audio
ABC
```

### SheetSage2Provider

May be implemented as a bounded subprocess provider using a fixed argv.

Future capability:

```text
music.transcribe
```

Do not use arbitrary shell execution.

### IrodoriProvider

May initially use the same bounded subprocess-provider pattern.

Future capability:

```text
speech.generate
```

The adapter can later change internally if Irodori is exposed as a persistent server.

## 13. MCP surface

Keep the MCP API small and capability/workflow oriented.

Target surface:

```text
system.health

capabilities.list
capabilities.get

models.list
models.get

workflows.list
workflows.build
workflows.save

jobs.submit
jobs.status
jobs.result
jobs.cancel

assets.list
assets.get
```

Do not create a separate top-level MCP tool for every runtime such as `generate_music` or `generate_speech` unless a later issue demonstrates a concrete need.

The primary execution flow remains:

```text
capability
    ↓
workflow
    ↓
job
    ↓
provider
    ↓
assets
```

## 14. Health model

`system.health` should eventually distinguish Hub health from runtime/provider availability.

Conceptual response:

```json
{
  "healthy": true,
  "busy": false,
  "active_job_id": null,
  "providers": [
    {
      "id": "comfyui",
      "available": true
    },
    {
      "id": "yue2",
      "available": false
    }
  ]
}
```

A stopped optional provider does not make the Hub itself unhealthy.

## 15. Recommended code direction

Long-term structure may evolve toward:

```text
src/flamoris_generation_mcp/

├── server.py
├── config.py

├── core/
│   ├── jobs.py
│   ├── assets.py
│   ├── capabilities.py
│   ├── resources.py
│   └── errors.py

├── providers/
│   ├── base.py
│   ├── registry.py
│   ├── comfyui.py
│   ├── yue2.py
│   ├── sheetsage2.py
│   └── irodori.py

├── workflows/
│   ├── store.py
│   ├── image.py
│   ├── video.py
│   ├── music.py
│   ├── speech.py
│   └── analysis.py

├── models/
│   └── catalog.py

└── runtime/
    └── health.py
```

This is a direction, not a mandate to perform a large file move in one change.

Prefer incremental extraction.

## 16. Implementation phases

### Phase A: Provider foundation

Introduce the provider-neutral architecture without adding a new runtime.

Goals:

- provider-neutral Job representation
- provider protocol/interface
- ProviderRegistry
- initial CapabilityRegistry foundation
- move existing ComfyUI execution behind `ComfyUIProvider`
- preserve current image-generation behavior
- preserve current stdio and Streamable HTTP behavior
- preserve current single-generation exclusivity
- preserve existing MCP tool compatibility

No YuE2, SheetSage2, Irodori, AnimeGen, or SeeThrough feature implementation is added in this phase.

### Phase B: YuE2 + SheetSage2

Use the provider foundation to prove multi-provider execution.

Candidate capabilities:

```text
music.plan
music.generate
music.transcribe
```

### Phase C: Irodori

Add:

```text
speech.generate
```

### Phase D: AnimeGen

Reuse ComfyUIProvider.

Add workflow/capability support for:

```text
video.image-to-video
video.motion-reference
```

### Phase E: SeeThrough

Reuse ComfyUIProvider.

Add:

```text
image.decompose
```

This phase also exercises multi-asset outputs such as PSD, metadata, preview images, and parts.

### Phase F: Runtime coordination

Consider automatic runtime lifecycle and shared-resource coordination only after multiple providers are working reliably.

## 17. Non-goals for the initial Hub expansion

Do not add:

- distributed job queues
- databases
- Redis
- multiple worker daemons
- automatic model downloads
- arbitrary shell execution
- automatic provider benchmarking
- automatic best-model selection
- remote cluster scheduling
- GPU memory prediction
- systemd lifecycle control
- configurable parallel generation

Initial target:

> one Hub / one machine / one authority / multiple providers

## 18. Central design rule

The Hub should be organized around:

```text
Capability
    ↓
Workflow
    ↓
Job
    ↓
Provider
    ↓
Assets
```

not around provider-specific commands.

Consumers should be able to request an operation such as:

```text
video.motion_reference
```

without depending on whether the implementation is AnimeGen, another future video runtime, or a different provider transport.

