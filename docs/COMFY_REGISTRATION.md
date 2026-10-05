# ChatGPT reference-image generation

Use `models.list` to choose an installed checkpoint, then `comfy.register` with
an API-format graph matching Controller's bounded checkpoint profile. Set
`LoadImage.image` to the literal `$reference_image`; the supported sample is in
the matching Controller commit's `examples/reference-image-comfy.json`.
`comfy.get` returns the normalized graph, defaults and immutable SHA-256 identity.

Upload the user's PNG/JPEG/WebP bytes through `inputs.upload.begin/write/finish`
with the existing bounded chunk/digest protocol. Build with the returned
definition ID as `template` and the returned input ID as `parameters.reference_image`.
Override prompt, size, seed, sampler and denoise through validated parameters.
Use `workflows.save` if needed, then `jobs.submit/status/result` and `assets.get`
for image retrieval. Never pass a caller path or URL as the image.

The image is an initial image, scaled with Lanczos without cropping and encoded
through the checkpoint VAE. This is img2img, not IPAdapter/ControlNet/reference
identity conditioning. Custom nodes and arbitrary graph topology are rejected.
Definitions are statically validated; registration does not certify live models.

Controller's `COMFYUI_INPUT_ROOT` must be an operator-configured shared
ComfyUI input directory with the retained input-copy ledger available. Copies
survive uncertain POSTs and are released only after definite rejection or terminal
observation. No automatic inference replay or live deployment is included.
Roll out the matching 25-tool Hub catalog with this facade and Controller pin.
