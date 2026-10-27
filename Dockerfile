FROM node:22-bookworm AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
COPY config ./config
COPY public ./public
COPY tests/*.cjs ./tests/
ARG VITE_PICHAT_EMBED_ORIGIN=http://127.0.0.1:9172
ARG VITE_PICHAT_AGENT_ID=geo-research-assistant
ARG VITE_PICHAT_PRODUCT_USER_ID=geoagent-local
RUN npm test && npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
ARG DEBIAN_MIRROR=https://deb.debian.org
# Finance and TACO explicitly use curl transport; Python dependencies alone
# cannot satisfy it. Fail the image build if the executable is unavailable.
RUN test "${DEBIAN_MIRROR#https://}" != "$DEBIAN_MIRROR" \
    && sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o APT::Update::Error-Mode=any \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl --version && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GEO_DATA_DIR=/app/data \
    COLLECTOR_DATA_ROOT=/app/data/collector-output
COPY requirements.txt ./
ARG PYPI_INDEX_URL=https://pypi.org/simple
RUN test "${PYPI_INDEX_URL#https://}" != "$PYPI_INDEX_URL" \
    && python -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir --index-url "$PYPI_INDEX_URL" -r requirements.txt
ENV PATH="/app/.venv/bin:${PATH}"
COPY backend ./backend
COPY scripts ./scripts
COPY start.ps1 stop.ps1 ./
COPY collectors ./collectors
COPY config ./config
COPY knowledge ./knowledge
COPY examples ./examples
COPY --from=frontend /build/dist ./dist
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["python", "-m", "scripts.serve", "--host", "0.0.0.0", "--port", "8000"]
