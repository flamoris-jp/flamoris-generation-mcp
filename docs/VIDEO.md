# AnimeGen / Wan video candidate

Controller [#31](https://github.com/flamoris-jp/flamoris-generation-controller/issues/31)
owns the resumed video implementation from [#25](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/25).
Generation MCP 1.2.0 pins the corresponding Controller source candidate and
keeps the same 25 tools and authenticated internal HTTP authority. There is
no new service, alternate execution engine or MCP-specific video job store.

## External contract

Use the existing `comfy.register/get`, `inputs.*`, `workflows.build/save`,
`jobs.submit/status/result/cancel` and `assets.list/prepare/read` operations.
`declaration` remains an ordinary JSON object, so this feature needs no new
tool or wire argument in the Hub's current generic static catalog.

The Controller [video contract](https://github.com/flamoris-jp/flamoris-generation-controller/blob/aec5cba84a5d9be0bbe62f831a77e1d00fb5f877/docs/VIDEO.md)
defines declaration v2, metadata v4, schema-11 saved recipes, `video.generate`,
the bounded `wan-video-v1` profile and exact output validation. That fixed
commit is published in Controller PR #32. It remains a source candidate;
production registration and real-machine acceptance are still pending.

I2V requires a managed `start_image`. FLF2V requires a second, distinct graph
binding for managed `end_image` and the actual `WanFirstLastFrameToVideo` node.
An end image is never silently discarded. Mode belongs to the immutable
definition; unexpected or missing role parameters reject before submission.
Image declaration v1 and existing images/native recipes remain compatible.

The facade passes declarations to Controller's installed-node/operator-policy
validation. It does not invent an AnimeGen graph, download models or switch
the GPU. A real successful API-format graph export and installed contracts
are required before production registration. Synthetic fixtures in tests are
not production graphs or evidence of model compatibility.

`jobs.status` reports provider state. Successful result/asset delivery additionally
requires a decoded MP4/H.264 matching the submitted dimensions, FPS, frame count
and timestamps. Use `assets.prepare/read` for bounded transfer and verify SHA-256.
A GIF, WebM, image preview, corrupt movie or another job's filename is rejected.
Existing unknown reservations, restart/no-replay, targeted cancellation, input
leases and output retention protections remain in effect.

## Candidate validation and live gate

The matching tests exercise MCP registration/upload/submission and authenticated
internal HTTP build/content transfer against one shared Controller, for both
modes. These tests use synthetic nodes and tiny locally encoded MP4s, no GPU.

The inspected live version remains 1.1.1, with ComfyUI OFF and Irodori READY.
The existing Kachinco project contains `red-shoes.webp`; it was not modified.
AnimeGen high/low LoRAs, Wan VAE and UMT5 are discoverable, but FP8 base models
are absent from the Generation diffusion-model/UNet catalog. This may be a
mount/catalog mismatch and does not prove the host lacks the weights.

Pending: successful API-format export, installed native SaveVideo output
receipt, base-model mapping, four-step Lightning bindings, launch setting
`--disable-pinned-memory`, matched candidate deployment, LIME Manager runtime
handoff, I2V real execution/MP4 playback and FLF2V endpoint/VRAM comparison.
Available connectors cannot read arbitrary provider files/export saved graphs
or edit operator configuration. No runtime switch or real AnimeGen generation
was attempted without those prerequisites. No Studio Video editor is included.
Existing Studio linked-result discovery/MP4 delivery remains the result path.

81 frames at 16 FPS encode to **5.0625 seconds**. Record that duration rather
than describing it as exactly five seconds or silently trimming a FLF2V final
pose. Keep #25 and Controller #31 open until actual acceptance is recorded.
