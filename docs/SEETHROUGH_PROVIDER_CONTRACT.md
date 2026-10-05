# SeeThrough and AnimeGen integration gates

This held source review belongs to #25. Decomposition/video have no current Generation implementation; custom graph registration and qualification were retired under AI #18. The pinned upstream output findings remain research, not a current authoring/rollout contract. It records the actual output transport before
adding `image.decompose` or video workflows. It does not register a workflow,
advertise a ready capability, or establish current runtime health.

## Pinned sources

| Component | Reviewed identity | Source authority |
| --- | --- | --- |
| SeeThrough custom nodes | `98d754bf04f668647919ab750eccb0e0640faa81` | [nodes.py](https://github.com/jtydhr88/ComfyUI-See-through/blob/98d754bf04f668647919ab750eccb0e0640faa81/nodes.py) |
| SeeThrough browser export | Same revision | [web/seethrough_psd.js](https://github.com/jtydhr88/ComfyUI-See-through/blob/98d754bf04f668647919ab750eccb0e0640faa81/web/seethrough_psd.js) |
| ComfyUI video output | `c194dd00cd42aa18d9dbf27d977bf6b85d9ea565` | [comfy_extras/nodes_video.py](https://github.com/Comfy-Org/ComfyUI/blob/c194dd00cd42aa18d9dbf27d977bf6b85d9ea565/comfy_extras/nodes_video.py) |

An earlier installation report records successful image decomposition and video
generation. That report establishes historical execution, not a reviewed
API-format production graph or an exact Generation readiness attestation. No
production API-format graph for these two workflows has been supplied yet.

## What SeeThrough Save PSD actually returns

At the pinned source, `SeeThrough_SavePSD` is an output node with
`RETURN_TYPES = ("STRING",)` and `RETURN_NAMES = ("info_file",)`. It writes:

- One PNG per retained semantic layer and an optional depth PNG for each layer.
- A `*_layers.json` document containing `prefix`, `timestamp`, `width`, `height`
  and the ordered `layers` list. Each layer has a name, filename, bounds and
  depth median; a depth filename is optional.
- A shared `seethrough_psd_info.log` containing the latest JSON filename.

The method returns the absolute JSON path in an ordinary result tuple. It does
not return ComfyUI `ui` output metadata and does not write a PSD file. Ordinary
execution-history image discovery therefore cannot identify these outputs.

The browser's Download PSD operation reads the shared latest-output log, fetches
the JSON and PNG files, and uses `ag-psd` in the browser to produce the PSD download.
That user interaction is a different artifact boundary from a server-owned
Generation asset. A filename extension accepted by the shared asset transport
does not make this browser operation an implemented provider output.

The shared log must not be used for provider completion, asset ownership, restart
recovery or deletion. A concurrent ComfyUI user can replace it, and it contains no
Generation job identity. Scanning for the newest JSON or matching an arbitrary
filename glob has the same ownership problem.

## Required integration shape

A separately authorized future decomposition adapter needs a minimal input/output
contract and the single retained job authority. The former custom graph registry
and input-injection path are absent and must not be reused or recreated by this
research document. The following are contract questions, not implementation steps:

1. Obtain the API-format graph used for a successful decomposition, with exact
   custom-node identities, model configuration and dependency links. The upstream
   `seethrough-basic.json` example is a UI-format authoring graph, not this receipt.
2. Replace the public image filename with a declared managed image binding.
   Any future adapter must use owned PNG/JPEG/WebP inputs with bounded leases and
   a separately reviewed provider staging contract; preserve source identity and digest. Clients cannot
   supply provider filenames, output prefixes or absolute paths.
3. Establish a job-scoped output receipt. A reviewed save-node extension may
   return the manifest through ComfyUI UI metadata, tied to the submitted prompt
   and declared output node. An independently reviewed native layered-save route
   is also possible. Both require their actual installed contracts and a real
   graph; neither is inferred from a node display name.
4. Assign the output prefix from the generation job ID and enforce it in the whole
   manifest. Accept only bounded relative basenames within that exact scope, reject
   duplicate references, traversal, absolute paths and symlinks, and confine local
   retention to the configured provider output root. A returned absolute path is
   never an asset ID or a trusted locator.
5. Validate a finite manifest, layer count, image dimensions, geometry and byte
   budget before publication. Retrieve only the declared files, decode PNGs, check
   role/cardinality expectations, and snapshot them into normal Hub assets. A
   successful provider execution without a valid manifest is not a successful
   decomposition result.
6. If PSD is required, add a reviewed server-side export contract or a verified
   native save node. `SeeThrough_PartsToLayers` converts parts into a `LAYERS`
   document; that conversion alone is not a persisted PSD. Record and validate
   the actual exporter output instead of promising a browser-generated artifact.
7. Preserve single-active-work reservation, uncertain submission handling,
   targeted cancellation and confirmed execution settlement. Apply provider
   retention only to owned staged inputs and declared outputs. Record exact
   readiness evidence through the normal verification boundary before Studio
   enables the workflow.

The save-node extension is a future provider-side change, not an available API in
this repository. Without a scoped manifest or another verified save contract,
`image.decompose` remains unavailable. Historical model execution does not waive
this output-identity gate.

The optional model loaders default to automatic downloads in upstream source.
Any reviewed production graph must explicitly disable automatic downloads and
pin local model selections. Offload configuration remains graph/runtime evidence;
Generation does not start services or switch GPU runtimes.

## AnimeGen video output

The pinned ComfyUI `SaveVideo` node exposes an explicit `filename_prefix`, saves
the selected container, and returns a video preview with a `SavedResult` that
contains filename, subfolder and output-folder type. This differs from
SeeThrough's bare result tuple. Its exact preview/history serialization and chosen
container must still be captured from the reviewed graph and an execution receipt.

The installation report's image-to-video settings are insufficient to recreate
the graph. In particular, do not infer sampler links, high/low-noise model order,
LoRA bindings, frame count or the save node from the model filenames. Acquire the
successful API-format export, bind the managed starting image explicitly, and
review its complete dependency graph before implementing its execution profile.

The profile must enforce finite duration/frame count, dimensions and steps, exactly
declared video outputs, safe job-owned prefixes, MIME/container validation and the
existing bounded large-asset transfer. A GIF/WebM or preview image is not silently
substituted for a declared MP4 result. Media transport support already present in
JobStore does not establish video-profile execution or readiness.

## Remaining acceptance

- Reviewed API-format exports for the successful SeeThrough and AnimeGen graphs.
- Job-scoped SeeThrough result manifest or verified native layered-save/export path.
- Exact ComfyUI history fixtures for the declared image/layer/video save nodes.
- Provider-specific semantic validation, manifest completeness and retention tests.
- Real managed-input, cancellation, restart and result-retrieval receipts under
  Generation authority, followed by exact readiness attestation and Studio use.

This review intentionally leaves the two capabilities disabled until those
contracts exist; music and speech providers can progress independently.
