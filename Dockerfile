# --- dashboard build ---
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build          # emits ../synapse/static -> /synapse/static

# --- runtime ---
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY synapse ./synapse
ARG EXTRAS=google
RUN pip install --no-cache-dir ".[${EXTRAS}]" && useradd -m synapse
COPY evals ./evals
COPY --from=web /synapse/static ./synapse/static
RUN mkdir -p /data /vault && chown -R synapse /app /data /vault
USER synapse
ENV SYNAPSE_DATA_DIR=/data SYNAPSE_VAULT_PATH=/vault SYNAPSE_HOST=0.0.0.0 \
    SYNAPSE_OLLAMA_URL=http://host.docker.internal:11434
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/api/health',timeout=4).json()['ok'] else 1)"
CMD ["python", "-m", "synapse.api"]
