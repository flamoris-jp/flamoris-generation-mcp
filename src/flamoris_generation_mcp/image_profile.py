"""Reviewed ComfyUI Image-v1 topology; never infer semantics from reachability alone."""


def image_topology(definition, graph=None):
    graph = graph if graph is not None else definition.graph
    active = set()
    visiting = set()

    def walk(key):
        if key in visiting:
            raise ValueError("Cyclic Image graph")
        if key in active:
            return
        visiting.add(key)
        for value in graph[key]["inputs"].values():
            if isinstance(value, list):
                if (
                    len(value) != 2
                    or not isinstance(value[0], str)
                    or value[0] not in graph
                    or type(value[1]) is not int
                    or value[1] < 0
                ):
                    raise ValueError("Invalid Image graph link")
                walk(value[0])
            elif isinstance(value, dict):
                raise ValueError("Unsupported Image graph input")
        visiting.remove(key)
        active.add(key)

    # Reject cycles/broken links even in unused nodes.
    for key in graph:
        walk(key)
    active.clear()
    walk(definition.output_node)

    def link(node, field, expected, index):
        value = graph[node]["inputs"].get(field)
        if not isinstance(value, list) or len(value) != 2 or value[1] != index:
            raise ValueError("Unsupported Image profile link")
        key = value[0]
        if key not in graph or graph[key]["class_type"] != expected:
            raise ValueError("Unsupported Image profile node")
        return key

    output = definition.output_node
    decoder = link(output, "images", "VAEDecode", 0)
    sampler = link(decoder, "samples", "KSampler", 0)
    loader = link(sampler, "model", "CheckpointLoaderSimple", 0)
    if link(decoder, "vae", "CheckpointLoaderSimple", 2) != loader:
        raise ValueError("Image profile requires a consistent checkpoint/VAE")
    positive = link(sampler, "positive", "CLIPTextEncode", 0)
    negative = link(sampler, "negative", "CLIPTextEncode", 0)
    for encoder in (positive, negative):
        clip = graph[encoder]["inputs"].get("clip")
        if clip == [loader, 1]:
            continue
        skip = link(encoder, "clip", "CLIPSetLastLayer", 0)
        if link(skip, "clip", "CheckpointLoaderSimple", 1) != loader:
            raise ValueError("Image profile requires a consistent checkpoint/CLIP")
        layer = graph[skip]["inputs"].get("stop_at_clip_layer")
        if type(layer) is not int or not -24 <= layer <= -1:
            raise ValueError("Unsupported Clip Skip configuration")
    if graph[positive]["inputs"]["clip"] != graph[negative]["inputs"]["clip"]:
        raise ValueError("Image profile requires a consistent Clip Skip path")
    bindings = {
        "checkpoint": (loader, "ckpt_name"),
        "positive_prompt": (positive, "text"),
        "negative_prompt": (negative, "text"),
        **{
            role: (sampler, field)
            for role, field in (
                ("seed", "seed"),
                ("steps", "steps"),
                ("cfg", "cfg"),
                ("sampler", "sampler_name"),
                ("scheduler", "scheduler"),
                ("denoise", "denoise"),
            )
        },
    }
    if definition.image.mode == "img2img":
        encoder = link(sampler, "latent_image", "VAEEncode", 0)
        if link(encoder, "vae", "CheckpointLoaderSimple", 2) != loader:
            raise ValueError("Image profile requires a consistent encoder/VAE")
        dimensions = link(encoder, "pixels", "ImageScale", 0)
        source = link(dimensions, "image", "LoadImage", 0)
        if graph[dimensions]["inputs"].get("crop") != "center" or graph[dimensions]["inputs"].get(
            "upscale_method"
        ) not in {
            "nearest-exact",
            "bilinear",
            "area",
            "bicubic",
            "lanczos",
        }:
            raise ValueError("img2img requires explicit center crop and resize")
        bindings["initial_image"] = (source, "image")
    else:
        dimensions = link(sampler, "latent_image", "EmptyLatentImage", 0)
        if (
            type(graph[dimensions]["inputs"].get("batch_size")) is not int
            or graph[dimensions]["inputs"]["batch_size"] != 1
        ):
            raise ValueError("Image profile requires a single image")
    bindings.update({role: (dimensions, role) for role in ("width", "height")})
    allowed_types = {
        "SaveImage",
        "VAEDecode",
        "KSampler",
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "CLIPSetLastLayer",
        "EmptyLatentImage",
        "LoadImage",
        "ImageScale",
        "VAEEncode",
    }
    if any(graph[key]["class_type"] not in allowed_types for key in active):
        raise ValueError("Unsupported effective Image topology")
    for spec in definition.parameters.values():
        binding = (spec.node, spec.input)
        for role in ("width", "height"):
            if binding == bindings[role] and (
                definition.image.dimensions.mode == "fixed" or spec.role != role
            ):
                raise ValueError("Dimension bindings require editable dimension roles")
        if spec.role is not None and (spec.node, spec.input) != bindings[spec.role]:
            raise ValueError("Image role must bind its effective semantic input")
        # Advanced scalar controls may use arbitrary public keys, but cannot mutate
        # the audited topology, crop, batch count or Clip Skip policy.
        if (spec.node, spec.input) not in bindings.values():
            raise ValueError("Unsupported Image profile parameter binding")
        if spec.type == "managed_input" and spec.role != "initial_image":
            raise ValueError("Image profile supports one initial_image only")
    # ComfyUI also executes disconnected OUTPUT_NODEs. A reviewed Image prompt
    # must contain exactly the declared output's dependency graph.
    if set(graph) != active:
        raise ValueError("Image profile forbids nodes outside declared output dependencies")
    for role in ("width", "height"):
        size = graph[dimensions]["inputs"].get(role)
        if type(size) is not int or not 64 <= size <= 4096 or size % 8:
            raise ValueError("Effective Image dimensions require bounded multiples of 8")
        if definition.image.dimensions.mode == "fixed" and size != getattr(
            definition.image.dimensions, role
        ):
            raise ValueError("Fixed Image size differs from effective graph")
    return {"sampler": sampler, "dimensions": dimensions, "active": active, "bindings": bindings}


def smoke_budget(definition, graph):
    topology = image_topology(definition, graph)
    size = graph[topology["dimensions"]]["inputs"]
    sampler = graph[topology["sampler"]]["inputs"]
    steps = sampler.get("steps")
    if (
        size["width"] > 512
        or size["height"] > 512
        or type(steps) is not int
        or not 1 <= steps <= 30
    ):
        raise ValueError("Verification smoke exceeds effective size/steps budget")
    return {"width": size["width"], "height": size["height"], "output_count": 1}
