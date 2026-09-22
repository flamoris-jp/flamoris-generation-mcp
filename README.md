# FLAMORIS Generation MCP

A small Python MCP-native Generation Hub. It currently generates images through
an existing ComfyUI service using trusted templates and user-facing parameters,
while keeping Hub jobs, capabilities and assets independent from provider HTTP
details. No .NET runtime or `flamoris-mcp-core` package is required.

Part of the [FLAMORIS Commons](https://github.com/flamoris-jp/flamoris-commons) ecosystem.

## Architecture

The process owns one `ProviderRegistry`, `CapabilityRegistry`, `WorkflowStore` and
`JobStore`. `JobStore` is the single authority for Hub job identity and the
single-generation reservation; providers execute work and normalize their own
execution IDs, states, errors and outputs behind a small provider interface.
ComfyUI is the only registered provider in this phase, but it is not the identity
of the Hub.

`system.health` reports Hub process health separately from provider availability.
A stopped ComfyUI instance makes the `comfyui` provider and its capabilities
unavailable, but does not make the Hub process unhealthy. `capabilities.list` and
`capabilities.get` expose the provider-independent operation `image.generate`
without automatically selecting a provider.

## Setup

Requires Python 3.11+ and an independently installed ComfyUI instance. No GPU or
ComfyUI installation is needed on the MCP host, but the host must be able to scan
the configured model directories. Model weights are neither downloaded nor loaded
by this server.

```sh
python -m venv .venv
# Activate the virtual environment for your shell, then:
python -m pip install -e '.[dev]'
flamoris-generation-mcp
```

The command starts a **stdio** MCP server; configure your MCP client to launch
`flamoris-generation-mcp` from the installed environment (or its resolved executable
path). The default stdio mode opens no HTTP listener. There is no authentication
system, web UI, or automatic dependency installer. In stdio mode, logs use stderr
and stdout is reserved for MCP messages.

Example MCP client configuration (adapt the outer format to your client):

```json
{
  "mcpServers": {
    "generation": {
      "command": "flamoris-generation-mcp",
      "env": {
        "FLAMORIS_COMFYUI_URL": "http://localhost:8188",
        "FLAMORIS_MODEL_ROOT": "./models",
        "FLAMORIS_WORKFLOW_DIR": "./.generation/workflows",
        "FLAMORIS_OUTPUT_DIR": "./.generation/outputs"
      }
    }
  }
}
```

Relative paths resolve against the MCP process's working directory, which clients
may choose differently. Set these variables to your own accessible directories.
Do not commit local configuration or weights. Use only a trusted ComfyUI endpoint;
provider redirects and environment HTTP proxies are disabled.

## Streamable HTTP

Select HTTP explicitly to serve the same tools on a local endpoint:

```sh
flamoris-generation-mcp --transport streamable-http
# Explicit equivalent, with configurable bind address, port and route:
flamoris-generation-mcp --transport streamable-http --host 127.0.0.1 --port 8765 --mcp-path /mcp
```

The default endpoint is `http://127.0.0.1:8765/mcp`. CLI options override the
corresponding environment variables below; otherwise defaults apply. With no
options or transport environment override, the command remains stdio-compatible.
The MCP path is a literal absolute URL path, such as `/mcp` or `/api/generation`,
without query parameters, fragments, route placeholders or a trailing slash
(except `/` itself). The module entrypoint accepts the same options.

A tunnel/reverse-proxy runtime may target this loopback HTTP endpoint. Install,
configure and authenticate that runtime separately; the Python package does not
manage tunnels or credentials. HTTP has no application authentication and every
connected client can access the same process-owned workflows/jobs. Keep external
access controlled by the deployment layer. SDK Host/Origin checks remain enabled
for the default loopback bind; the forwarding runtime must send headers accepted
by that local endpoint (an external Host/Origin can be rejected). No proxy or
authentication middleware is added here.

Run one server process with one selected transport. Both startup modes use the
same `MCPServer` factory, validation, stores and provider registry. HTTP requests and
client sessions share that process's state; disconnecting a client does not erase
jobs or close the provider. Separate processes do not share in-memory state, so
multiple workers/replicas and simultaneous stdio/HTTP listeners are not provided.
ComfyUI's URL remains independently configured by `FLAMORIS_COMFYUI_URL`.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `FLAMORIS_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`; CLI `--transport` |
| `FLAMORIS_HTTP_HOST` | `127.0.0.1` | HTTP bind hostname/IP; CLI `--host` |
| `FLAMORIS_HTTP_PORT` | `8765` | HTTP port (1–65535); CLI `--port` |
| `FLAMORIS_MCP_PATH` | `/mcp` | Literal HTTP route; CLI `--mcp-path` |
| `FLAMORIS_COMFYUI_URL` | `http://localhost:8188` | ComfyUI HTTP base URL; path prefixes supported |
| `FLAMORIS_MODEL_ROOT` | `models` | Root containing the model-kind subdirectories below |
| `FLAMORIS_MODEL_DIRS` | unset | JSON map from model kind to a list of scan roots; overrides that kind |
| `FLAMORIS_WORKFLOW_DIR` | `.generation/workflows` | Saved parameter recipes |
| `FLAMORIS_OUTPUT_DIR` | `.generation/outputs` | Downloaded outputs and metadata, grouped by job ID |
| `FLAMORIS_REQUEST_TIMEOUT` | `30` | HTTP timeout in seconds (greater than 0, at most 300) |
| `FLAMORIS_TARGETED_INTERRUPT` | `false` | Enable running cancellation only for a provider with prompt-ID-scoped `/interrupt` |

Model kinds map to these subdirectories:

| Kind | Subdirectory |
| --- | --- |
| `checkpoint` | `checkpoints` |
| `lora` | `loras` |
| `vae` | `vae` |
| `controlnet` | `controlnet` |
| `clip` | `clip` |
| `clip_vision` | `clip_vision` |
| `diffusion_model` | `diffusion_models` |
| `text_encoder` | `text_encoders` |
| `unet` | `unet` |

For example, `FLAMORIS_MODEL_DIRS='{"lora":["./model-library/styles"]}'`
overrides the LoRA directory. Missing directories are treated as empty. Recursive
scans recognize `.safetensors`, `.ckpt`, `.pt`, `.pth`, `.bin`, and `.gguf` files.
Names are relative POSIX paths; IDs are `kind:name`. Duplicate names across roots
of the same kind are rejected as ambiguous. Symlinks escaping a root are ignored;
configure the actual external directory as a root instead.

**Discovery does not establish model compatibility.** Use checkpoint models that
work with ComfyUI's built-in `CheckpointLoaderSimple`, `CLIPTextEncode`, and
`EmptyLatentImage`, with compatible LoRAs. This is suitable for ordinary SD1.x/SDXL
checkpoint experiments. Separate diffusion/text-encoder models, ControlNet and
GGUF may be discoverable but have no execution templates in Phase 1. ComfyUI must
resolve the same relative checkpoint/LoRA names, e.g. through its configured model
directories or `extra_model_paths.yaml`. Its API validates sampler/scheduler names
and actual node/model compatibility on submission.

## Tools and first generation

| Tool | Arguments | Result |
| --- | --- | --- |
| `system.health` | none | Process health and separate provider availability/queue counts |
| `capabilities.list` | none | Provider-independent operations and current availability |
| `capabilities.get` | `capability_id` | One capability, runtime/provider identity and workflow templates |
| `models.list` | optional `kind` | Installed file metadata; no weight deserialization |
| `models.get` | `model_id` | One installed model |
| `workflows.list` | none | Templates and built/saved workflow IDs |
| `workflows.build` | `template`, `parameters` | Workflow ID, normalized recipe and executable prompt |
| `workflows.save` | `workflow_id` | Persist a recipe, preserving its ID |
| `jobs.submit` | `workflow_id` | New job ID, or a busy error while another generation is active |
| `jobs.status` | `job_id` | Poll execution state |
| `jobs.result` | `job_id` | Metadata; when complete, local output files and metadata JSON |
| `jobs.cancel` | `job_id` | Cancellation result or an explicit running-cancellation limitation |
| `assets.list` | `job_id` | Metadata-only stable asset IDs; never downloads payloads |
| `assets.get` | `asset_id` | MCP-native binary media content for one generated asset |

1. Call `system.health` and optionally `capabilities.list`, then `models.list` for
   `checkpoint` and `lora`.
2. Call `workflows.build` with installed relative names, for example:

```json
{
  "template": "text-to-image-lora",
  "parameters": {
    "checkpoint": "example.safetensors",
    "positive_prompt": "watercolor flowers on a quiet windowsill",
    "negative_prompt": "blurry",
    "width": 512,
    "height": 512,
    "seed": 42,
    "steps": 20,
    "cfg": 7,
    "sampler": "euler",
    "scheduler": "normal",
    "denoise": 1,
    "loras": [
      {"name": "style.safetensors", "strength_model": 0.8, "strength_clip": 0.6},
      {"name": "detail.safetensors", "strength_model": 0.4, "strength_clip": 0.3}
    ]
  }
}
```

3. Optionally call `workflows.save` with the returned `workflow_id`.
4. Call `jobs.submit` with that ID, poll `jobs.status`, then call `jobs.result`.
5. For a completed job, call `assets.list` with the job ID, then pass one returned
   `asset_id` to `assets.get`. PNG/JPEG/WebP outputs are returned as MCP image
   content, so remote clients receive the media bytes rather than a host-only
   filesystem path.

The asset layer is intentionally media-oriented rather than filesystem-oriented.
Asset IDs identify outputs owned by known in-process generation jobs; callers cannot
supply arbitrary file paths. `assets.list` is metadata-only: an output that has not
been downloaded reports `size_bytes: null` and `materialized: false`.
`assets.get` materializes only the requested asset. The current provider emits
images, while the same `assets.list` / `assets.get` surface can be extended later
for video and audio content without exposing the output filesystem.

Use `text-to-image` with no LoRAs for a plain checkpoint workflow. The LoRA
template requires at least one LoRA, preserves order, and chains both model and
CLIP through every entry. Seed is an explicit nonnegative integer (default 0),
not a randomized sentinel. Width/height must be multiples of 8 from 64 to 4096;
steps range from 1 to 150; at most 16 LoRAs are accepted. These bounds do not
guarantee sufficient provider VRAM.

Returned raw prompts are inspectable exports, **not mutable submission inputs**.
`jobs.submit` accepts only a workflow ID and rebuilds the known template from its
recipe, rechecking installed models. Saved files contain only versioned recipes.

## Job behavior and limits

- One server process permits one active generation at a time. While a job is queued,
  running, submitting, or otherwise non-terminal, another `jobs.submit` fails
  immediately with a bounded busy error. The server does not add a waiting queue.
  Status, result, cancellation, model and workflow inspection remain available.
  Capacity is released only after terminal completion, failure or cancellation is
  observed; stdio and Streamable HTTP share the same process-owned guard.
- States: `submitting`, `queued`, `running`, `completed`, `failed`, `cancelled`,
  `cancel_requested`, `unknown`. A missing queue/history entry is `unknown`, not
  successful completion. Errors include node ID/type where available without
  returning provider tracebacks. Transient HTTP failures are MCP tool errors and
  do not overwrite a job's execution state.
- Jobs and unsaved workflows belong to one server process; there is no persistent
  job queue or recovery after restart. Each session holds up to 1024 of each.
  Save recipes before restart; retain completed result files/metadata. Process
  shutdown does not cancel already submitted ComfyUI work.
- Submission is not idempotent. HTTP POST is never retried automatically. After
  an ambiguous timeout, inspect the provider queue before manually resubmitting.
- Queued cancellation deletes only the requested prompt. Running cancellation
  defaults to unsupported because older ComfyUI versions ignore `prompt_id` and
  interrupt globally. Set `FLAMORIS_TARGETED_INTERRUPT=true` only after verifying
  your provider supports targeted interruption. There is no global-interrupt
  fallback. An accepted request remains `cancel_requested` until history confirms
  a terminal state; completion can win a cancellation race.
- `jobs.result` copies images from ComfyUI's `/view` into local output storage;
  it does not change the provider's own output directory. Downloads are atomic,
  retryable and reused on subsequent calls, limited to 64 MiB per image and 64
  images per result. Local names are generated, not trusted provider paths. Existing
  `jobs.result` file-path metadata remains available for local inspection. Remote
  clients should use `assets.list` / `assets.get` to receive generated media through
  MCP. Asset reads are limited to completed known jobs, checked against the configured
  output root, reject symlinks/path escapes, and are bounded to 64 MiB per asset.
- Reproducibility metadata contains the operation, provider ID, provider-owned
  execution ID, template, all parameters (including
  prompts, checkpoint, ordered LoRAs and seed), workflow/job IDs, status and output
  references. No weight hashes are calculated. Replacing weights under the same
  filename or changing provider versions can change results.

## Development

```sh
python -m pytest
ruff check .
ruff format --check .
python -m build
```

Tests use temporary model files and mocked HTTP, plus real MCP client/server
protocol calls. CI needs neither live ComfyUI nor a GPU. The official MCP Python
SDK 2.x owns protocol handling; dependencies are bounded to compatible majors.

To add a template, extend the explicit `Template` type, template descriptions and
trusted builder in `workflows.py`, add typed parameters where necessary, and test
the emitted graph and rejection paths. Templates are shipped with the Python
package; the workflow directory stores recipes only. Do not add dynamic code
loading or raw node mutation tools. Provider-specific execution belongs behind
`providers/`; ComfyUI HTTP parsing remains isolated in `comfyui.py`.

API references: [ComfyUI server routes](https://docs.comfy.org/development/comfyui-server/comms_routes),
[ComfyUI server implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py),
[official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).


## License

Code in this repository is licensed under the [Apache License 2.0](LICENSE), unless otherwise noted.

Commercial use does not require permission. If you'd like, we'd be happy to hear what you used FLAMORIS for. This is completely optional.

FLAMORIS software is provided as-is and does not include guaranteed individual support. AI-assisted self-support is encouraged.

AI models, model weights, datasets, generated media, and other non-code assets are not automatically covered by this repository's license. Their applicable licenses and usage terms must be checked separately.

If FLAMORIS helps you or you find it interesting, your support helps fund development and keeps the project growing. 🌱  
<sub>Mostly GPU bills.</sub>

---

## 日本語

FLAMORIS Generation MCPは、画像・動画・音楽・音声などの生成をMCPから扱うための小さなPythonサーバーです。

勝手に使ってください。  
改造しても、組み込んでも、面白いものや変なものを作ってもOKです。

商用作品や製品で使う場合も、許可は不要です。  
もしよければ「こんなのに使ったよ」と教えてもらえるとうれしいです。もちろん強制ではありません。

FLAMORISのソフトウェアは現状のまま提供され、個別サポートや動作保証はありません。困ったときは、README、Issue、テスト、ソースコードをAIに読ませて自己サポートしてください。

このリポジトリのコードはApache License 2.0です。AIモデル、model weights、データセット、生成物、その他の非コード資産には別のライセンスや利用条件が適用される場合があるため、それぞれ確認してください。

もしお役に立てたり、面白いと思っていただけたなら、開発費用をご支援いただけるとうれしいです。  
FLAMORISは元気になって育ちます。🌱  
<sub>主にGPU代とか。</sub>
