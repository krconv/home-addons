# Home Assistant base image (Alpine + s6-overlay)
ARG BUILD_FROM=ghcr.io/home-assistant/{arch}-base:latest
FROM $BUILD_FROM

# Fail the container if any s6 service fails
ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2

# Build args (change these at build time if you want a different fork/ref)
ARG REPO_URL="https://github.com/aradzie/keybr.com"
ARG REPO_REF="master"

# Install toolchain to build the static site and serve it
RUN apk add --no-cache git nodejs npm nginx

# Build keybr.com
WORKDIR /build
RUN git clone --depth=1 -b "${REPO_REF}" "${REPO_URL}" src
WORKDIR /build/src
# Use ci to respect package-lock; then produce production build
RUN npm ci && npm run build

# Move built assets to webroot
RUN mkdir -p /var/www/html && cp -r dist/* /var/www/html/

# Add service & nginx config via rootfs
COPY rootfs/ /

# Expose the port Home Assistant will proxy to
EXPOSE 8080
