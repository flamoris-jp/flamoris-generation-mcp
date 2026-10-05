#!/usr/bin/env bash
set -euo pipefail

image="generation-mcp-smoke:${GITHUB_RUN_ID:-local}"
scratch=$(mktemp -d)
container=""
cleanup() {
  if [ -n "$container" ]; then
    # Stop the authority before deleting its private test-only lock directory.
    docker rm -f "$container" >/dev/null 2>&1 || return
    docker run --rm --user 0:0 --entrypoint python \
      -v "$scratch/outputs:/smoke-output" "$image" -c \
      'import shutil; shutil.rmtree("/smoke-output/controller-authority", ignore_errors=True)'
  fi
  rm -rf "$scratch"
}
trap cleanup EXIT

mkdir -p "$scratch/models/checkpoints" "$scratch/workflows" "$scratch/outputs"
chmod 755 "$scratch"
chmod 777 "$scratch/workflows" "$scratch/outputs"
printf 'sample' > "$scratch/models/checkpoints/example.safetensors"

# Parse the shipped Compose sample with non-default interpolation values before
# exercising the image independently; this catches a stale or invalid example.
MODEL_ROOT="$scratch/models" WORKFLOW_ROOT="$scratch/workflows" \
  OUTPUT_ROOT="$scratch/outputs" FLAMORIS_HTTP_PORT=9876 \
  FLAMORIS_MCP_PATH=/review/mcp docker compose -f compose.yaml config --format json |
  python -c '
import json
import sys

service = json.load(sys.stdin)["services"]["generation-mcp"]
assert str(service["environment"]["FLAMORIS_HTTP_PORT"]) == "9876"
assert service["environment"]["FLAMORIS_MCP_PATH"] == "/review/mcp"
assert str(service["ports"][0]["published"]) == "9876"
assert service["ports"][0]["host_ip"] == "127.0.0.1"
mounts = {volume["target"]: volume for volume in service["volumes"]}
assert mounts["/data/models"]["read_only"] is True
assert "/data/definitions" not in mounts
assert "FLAMORIS_WORKFLOW_DEFINITION_DIR" not in service["environment"]
assert mounts["/data/workflows"].get("read_only", False) is False
assert mounts["/data/outputs"].get("read_only", False) is False
'

docker build -t "$image" .
container=$(docker run -d --read-only --tmpfs /tmp:mode=1777 \
  -p 127.0.0.1::8765 \
  -e FLAMORIS_COMFYUI_URL=http://127.0.0.1:1 \
  -e FLAMORIS_CONTROLLER_TOKEN=fixture-docker-controller-token-32 \
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
from urllib.request import Request, urlopen
from urllib.error import HTTPError

with urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=2) as response:
    assert response.status == 200
    assert json.load(response)["healthy"] is True
endpoint = f"http://127.0.0.1:{sys.argv[1]}/api/v1/generation/system.health"
request = Request(endpoint, b"{}", {"Content-Type": "application/json"})
try:
    urlopen(request, timeout=2)
except HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError("internal API accepted an unauthenticated call")
request.add_header("Authorization", "Bearer fixture-docker-controller-token-32")
with urlopen(request, timeout=5) as response:
    assert json.load(response)["controller"]["api_version"] == 1
PY

docker exec -i "$container" python - <<'PY'
import json
from pathlib import Path

models = Path('/data/models/checkpoints/example.safetensors')
from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.workflows import WorkflowStore
settings = Settings.from_env()
store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
assert store.list()["definitions"] == []
built = store.build("text-to-image", {"checkpoint": "example.safetensors", "positive_prompt": "smoke"})
assert store.save(built["workflow_id"])
try:
    store.build("basic-image", {"checkpoint": "example.safetensors", "positive_prompt": "smoke"})
except ValueError as error:
    assert "retired" in str(error)
else:
    raise AssertionError("custom recipe executed")
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
