# flamoris-generation-mcp v1.0.4

Fix the missing Updater compatibility declaration in 1.0.3. The catalog now
accepts updates from 1.0.2 and 1.0.3 while preserving operator runtime.env,
saved settings, existing mounts, Job storage and Workflow/provider files.
CI installs the published 1.0.2 image and upgrades it to this release, checking
retained bytes/mappings and installed application/component versions.

Controller 1.0.1 and optional FLAMORIS_YUE2_OUTPUT_ROOT are unchanged from
1.0.3. The output directory and its container binding remain operator settings;
updating alone does not enable publication, create a media root, move existing
files or activate a GPU provider. Updater 1.0.9 or newer is required.

Both CPU images, catalogs and checksums are published without overwriting older
releases. Real-host update and configured media publication are separate checks.
