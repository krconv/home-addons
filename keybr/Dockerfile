# Home Assistant base image (Supervisor passes BUILD_FROM)
ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2

# Build args for upstream
ARG REPO_URL="https://github.com/aradzie/keybr.com"
ARG REPO_REF="master"

# Tooling to build and serve
RUN apk add --no-cache git nodejs npm nginx

# Get sources
WORKDIR /build
RUN git clone --depth=1 -b "${REPO_REF}" "${REPO_URL}" src

# Build the SPA
WORKDIR /build/src
RUN npm ci && npm run build

# Serve built assets
RUN mkdir -p /var/www/html && cp -r /build/src/dist/* /var/www/html/

# Add s6 service + nginx config
COPY rootfs/ /

EXPOSE 8080
