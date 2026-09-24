#!/usr/bin/env bash
set -euo pipefail

image="generation-mcp-smoke:${GITHUB_RUN_ID:-local}"
scratch=$(mktemp -d)
container=""
cleanup() {
  if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  rm -rf "$scratch"
}
trap cleanup EXIT

mkdir -p "$scratch/models/checkpoints" "$scratch/workflows" "$scratch/outputs"
chmod 755 "$scratch"
chmod 777 "$scratch/workflows" "$scratch/outputs"
printf 'sample' > "$scratch/models/checkpoints/example.safetensors"

docker build -t "$image" .
container=$(docker run -d --read-only --tmpfs /tmp:mode=1777 \
  -p 127.0.0.1::8765 \
  -e FLAMORIS_COMFYUI_URL=http://127.0.0.1:1 \
  -v "$scratch/models:/data/models:ro" \
  -v "$scratch/workflows:/data/workflows" \
  -v "$scratch/outputs:/data/outputs" "$image")

for attempt in $(seq 1 45); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$container")
  if [ "$state" = healthy ]; then break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$container")" != true ]; then
    docker logs "$container"
    exit 1
  fi
  sleep 1
done
test "$(docker inspect -f '{{.State.Health.Status}}' "$container")" = healthy
test "$(docker inspect -f '{{.Config.User}}' "$container")" = 10001:10001

port=$(docker port "$container" 8765/tcp | sed -n 's/^127\.0\.0\.1://p')
python - "$port" <<'PY'
import json
import sys
from urllib.request import urlopen

with urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=2) as response:
    assert response.status == 200
    assert json.load(response)["healthy"] is True
PY

docker exec -i "$container" python - <<'PY'
from pathlib import Path

models = Path('/data/models/checkpoints/example.safetensors')
assert models.read_text() == 'sample'
try:
    models.write_text('changed')
except OSError:
    pass
else:
    raise AssertionError('model mount is writable')
Path('/data/workflows/smoke').write_text('workflow')
Path('/data/outputs/smoke').write_text('output')
PY
test "$(cat "$scratch/models/checkpoints/example.safetensors")" = sample
test "$(cat "$scratch/workflows/smoke")" = workflow
test "$(cat "$scratch/outputs/smoke")" = output
test "$(docker inspect -f '{{.State.Running}}' "$container")" = true
echo 'Docker Streamable HTTP, liveness, non-root and mounts PASS'
