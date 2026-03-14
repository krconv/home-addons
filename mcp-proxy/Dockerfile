ARG BUILD_FROM

# ── Build stage ──────────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS builder

WORKDIR /build
COPY app/ .

RUN yarn install --frozen-lockfile && \
    yarn build && \
    rm -rf node_modules && \
    yarn install --frozen-lockfile --production

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/node /usr/local/bin/node
COPY --from=builder /build/dist       /opt/mcp-proxy/dist
COPY --from=builder /build/node_modules /opt/mcp-proxy/node_modules
COPY --from=builder /build/package.json /opt/mcp-proxy/package.json

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
