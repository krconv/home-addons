ARG BUILD_FROM

# ── Build stage: clone repo and build the Node.js app ──
FROM node:22-bookworm-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/krconv/quicken-simplifi-mcp.git /build
WORKDIR /build

RUN yarn install --frozen-lockfile && \
    yarn build && \
    rm -rf node_modules && \
    yarn install --frozen-lockfile --production

# ── Runtime stage: HA base image with Node, nginx, and the built app ──
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      jq \
      nginx && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/node /usr/local/bin/node
COPY --from=builder /build/dist /opt/simplifi-mcp/dist
COPY --from=builder /build/node_modules /opt/simplifi-mcp/node_modules
COPY --from=builder /build/package.json /opt/simplifi-mcp/package.json

COPY nginx.conf /etc/nginx/sites-available/default
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
