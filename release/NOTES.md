# flamoris-generation-mcp v1.0.3

Controller 1.0.1 is embedded in this release. Optional operator-only
`FLAMORIS_YUE2_OUTPUT_ROOT` publishes completed Music bundles (`audio.wav`,
`score.abc`, `replay.json`) into an existing configured media directory.
Private Job/Workflow storage and default behavior remain unchanged. ComfyUI
image originals keep their provider destination; no host-specific path is built in.

Publication copies are independent from API archives, group-readable under the
operator's permissions and never overwritten or recreated after operator deletion.
A missing/symlink/colliding destination reports publication failure without
replaying or failing completed generation. See docs/MUSIC_PROVIDERS.md and the
Controller storage guide for configuration and deletion boundaries.

Both amd64/arm64 images, Updater catalogs and SHA-256 checksums are published by
this repository. The optional media mount requires deployment configuration;
the fresh-install recipe does not add it automatically. This catalog retains
`compatible_from=[]`; it does not declare an automatic upgrade from older
installed layouts. Release publication does not update hosts, relocate models
or move/delete existing output data.
