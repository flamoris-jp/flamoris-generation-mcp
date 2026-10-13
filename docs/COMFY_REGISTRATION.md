# ComfyWorkFlow registration and reference images

The matched Controller implements checkpoint txt2img/img2img and reviewed IPAdapter appearance/style plus prepared-map ControlNet profiles. Generation MCP exposes the same comfy.register/get, workflows.build, input upload/snapshot, job and Asset operations through its existing 25-tool surface.

See [Controller reference setup and examples](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/REFERENCE_IMAGES.md) for concrete graphs, the graph-writing CLI, model pairing, shared ComfyUI input storage and Studio integration. The definition digest pins graph/model identity; registration does not test model compatibility or output quality.

The retained img2img uses schema 7 and reference_image. Conditioning uses schema 8 with initial_image, character_image, style_image and control_image as declared. All are immutable managed input IDs. Influence and start/end values are validated; missing/unsupported roles are rejected. ControlNet requires a prepared map; no automatic pose/depth extraction is added.

models.list now accepts ipadapter in addition to existing model kinds. Refresh the Hub's matched 25-tool catalog; no new namespace or internal Hub route is introduced. Configure nodes/models in ComfyUI, mount an explicitly shared FLAMORIS_COMFYUI_INPUT_ROOT into Generation, and keep Controller state outside it. No model download, GPU activation or deployment is performed by graph registration.

Retired schema 2/3, v3, automatic graph qualification and Runtime delegation remain unavailable. Reconcile new schema-8 recipes/work and protected copies before downgrade. The new package target is 1.0.3, prepared but not published by this source change.

