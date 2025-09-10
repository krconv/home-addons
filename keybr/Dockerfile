# Home Assistant base image (Supervisor passes BUILD_FROM)
ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2

# Build args for upstream
ARG REPO_URL="https://github.com/aradzie/keybr.com"
ARG REPO_REF="master"

# Tooling to build and serve
RUN apk add --no-cache git nodejs npm nginx

# Fetch source
WORKDIR /opt
RUN git clone --depth=1 -b "${REPO_REF}" "${REPO_URL}" keybr
WORKDIR /opt/keybr

# Build the SPA
RUN npm ci && npm run build

RUN npm install -g tsx && chmod +x ./packages/devenv/lib/initdb.ts

# Add s6 service + nginx config
COPY rootfs/ /

EXPOSE 8080
