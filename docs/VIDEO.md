# AnimeGen / Wan video candidate

Controller [#31](https://github.com/flamoris-jp/flamoris-generation-controller/issues/31)
owns the resumed video implementation from [#25](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/25).
Generation MCP 1.2.1 pins the corresponding Controller source candidate and
keeps the same 25 tools and authenticated internal HTTP authority. There is
no new service, alternate execution engine or MCP-specific video job store.

## External contract

Use the existing `comfy.register/get`, `inputs.*`, `workflows.build/save`,
`jobs.submit/status/result/cancel` and `assets.list/prepare/read` operations.
`declaration` remains an ordinary JSON object, so this feature needs no new
tool or wire argument in the Hub's current generic static catalog.

The Controller [video contract](https://github.com/flamoris-jp/flamoris-generation-controller/blob/fd31bf6957dedb2958f673b1d275871c016f0917/docs/VIDEO.md)
defines declaration v2, metadata v4, schema-11 saved recipes, `video.generate`,
the bounded `wan-video-v1` profile and exact output validation. That fixed
commit is published in Controller PR #33. The 1.2.0 base was released;
production registration and real-machine acceptance are still pending.

I2V requires a managed `start_image`. FLF2V requires a second, distinct graph
binding for managed `end_image` and the actual `WanFirstLastFrameToVideo` node.
An end image is never silently discarded. Mode belongs to the immutable
definition; unexpected or missing role parameters reject before submission.
Image declaration v1 and existing images/native recipes remain compatible.

The facade passes declarations to Controller's installed-node/operator-policy
validation. It does not invent an AnimeGen graph, download models or switch
the GPU. Review the actual graph candidate and installed contracts before production
registration; a successful GPU run is a separate acceptance step. Synthetic fixtures in tests are
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

Generation MCP / Controller 1.2.0 were deployed, and ComfyUI was activated
through LIME Manager. The operator preflight confirms the installed native
SaveVideo V3 schema and both FP8 weights on the host. The current node policy
excludes required video classes, and the model links resolve outside Generation's
model mount. These require explicit operator configuration. Version 1.2.0 did
not support this SaveVideo schema; 1.2.1 accepts only its installed MP4/H.264
branch with constant API inputs `format=mp4` and `format.codec=h264`.
General dynamic inputs, optional encoding branches and nested JSON stay rejected.
Legacy static output graphs keep their original identity and representation.

Matching tests use the native SaveVideo/CreateVideo metadata receipt with
synthetic conditioning nodes and tiny encoded MP4s. Both legacy and V3 output
forms run through the same MCP/HTTP authority. A reconstructed public-source
AnimeGen graph candidate passes installed-metadata validation locally; no real
AnimeGen job or GPU result is claimed. The Kachinco project remains unchanged.

Pending: operator policy/model visibility, launch flag verification,
matched 1.2.1 deployment, managed production input, actual I2V execution/MP4
playback, and FLF2V endpoint/VRAM comparison. Available connectors cannot edit
arbitrary host files. No Studio Video editor is included. Existing Studio
linked-result discovery/MP4 delivery remains the result path.

81 frames at 16 FPS encode to **5.0625 seconds**. Record that duration rather
than describing it as exactly five seconds or silently trimming a FLF2V final
pose. Keep #25 and Controller #31 open until actual acceptance is recorded.
