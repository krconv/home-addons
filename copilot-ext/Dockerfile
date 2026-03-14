ARG BUILD_FROM

# ── Build stage: clone repo and build the Node.js app ──
FROM node:22-bookworm-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN git init /build && \
    git -C /build remote add origin https://github.com/krconv/copilot-ext.git && \
    git -C /build fetch --depth 1 origin 5c74081158f106ad0f3561efe8fed6e838f3b9f1 && \
    git -C /build checkout FETCH_HEAD
WORKDIR /build

RUN yarn install --frozen-lockfile && \
    yarn build && \
    rm -rf node_modules && \
    yarn install --frozen-lockfile --production

# ── Runtime stage: HA base image with Node and the built app ──
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
      jq && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/node /usr/local/bin/node
COPY --from=builder /build/dist /opt/copilot-ext/dist
COPY --from=builder /build/node_modules /opt/copilot-ext/node_modules
COPY --from=builder /build/package.json /opt/copilot-ext/package.json

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
