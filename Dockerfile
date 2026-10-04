FROM python:3.12-slim-bookworm AS build

WORKDIR /build
COPY pyproject.toml README.md LICENSE docker-requirements.txt ./
COPY src/ ./src/
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels -r docker-requirements.txt \
    && python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLAMORIS_HTTP_HOST=0.0.0.0 \
    FLAMORIS_HTTP_PORT=8765 \
    FLAMORIS_MODEL_ROOT=/data/models \
    FLAMORIS_WORKFLOW_DIR=/data/workflows \
    FLAMORIS_OUTPUT_DIR=/data/outputs

COPY --from=build /wheels /wheels
COPY docker-requirements.txt /tmp/docker-requirements.txt
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels \
      -r /tmp/docker-requirements.txt /wheels/flamoris_generation_mcp-*.whl \
    && rm -rf /wheels /tmp/docker-requirements.txt \
    && mkdir -p /data/models /data/workflows /data/outputs \
    && chown -R 10001:10001 /data

USER 10001:10001
WORKDIR /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-m", "flamoris_generation_mcp.healthcheck"]
CMD ["flamoris-generation-mcp", "--transport", "streamable-http"]
