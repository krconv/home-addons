# syntax=docker/dockerfile:1

##### Global (must be before any FROM using it) #####
ARG BUILD_FROM

# Builder versions (Debian toolchain for reliability)
ARG ELIXIR_VERSION=1.18.4
ARG OTP_VERSION=27.2.4
ARG DEBIAN_VERSION=bookworm-20250428-slim
ARG PINCHFLAT_REF=master   # set to a tag like v2025.6.6 for reproducible builds

##### -------- Builder (Debian) -------- #####
FROM hexpm/elixir:${ELIXIR_VERSION}-erlang-${OTP_VERSION}-debian-${DEBIAN_VERSION} AS builder
ARG TARGETPLATFORM
ARG PINCHFLAT_REF
ENV MIX_ENV=prod ERL_FLAGS="+JPperf true"

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      build-essential git curl ca-certificates xz-utils && \
    curl -sL https://deb.nodesource.com/setup_20.x -o /tmp/node.sh && \
    bash /tmp/node.sh && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g yarn && \
    mix local.hex --force && mix local.rebar --force && \
    rm -rf /var/lib/apt/lists/*

# Optional: FFmpeg/ffprobe in builder (not used at runtime on Alpine)
RUN set -eux; \
  case "${TARGETPLATFORM:-linux/amd64}" in \
    "linux/arm64") FFMPEG_URL="https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linuxarm64-gpl.tar.xz" ;; \
    *)             FFMPEG_URL="https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linux64-gpl.tar.xz" ;; \
  esac; \
  curl -L "$FFMPEG_URL" -o /tmp/ffmpeg.tar.xz && \
  tar -xf /tmp/ffmpeg.tar.xz --strip-components=2 --no-anchored -C /usr/local/bin ffmpeg ffprobe && \
  rm -f /tmp/ffmpeg.tar.xz

# Pull Pinchflat source and build release
WORKDIR /src
RUN git clone --depth 1 --branch "${PINCHFLAT_REF}" https://github.com/kieraneglin/pinchflat ./
RUN yarn --cwd assets install && \
    mix deps.get --only prod && \
    mix deps.compile && \
    mix assets.deploy && \
    mix compile && \
    mix release

##### -------- Runtime (Home Assistant add-on base: Alpine) -------- #####
FROM $BUILD_FROM AS runner
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

# Minimal runtime deps (musl/Alpine)
# - Use Alpine ffmpeg (yt-dlp static builds are glibc; don’t copy from builder)
RUN apk add --no-cache \
      bash curl python3 ffmpeg ca-certificates tzdata && \
    update-ca-certificates

# App files
WORKDIR /app
COPY --from=builder /src/_build/prod/rel/pinchflat ./

# Persist & paths for HA
RUN mkdir -p /data /media/youtube /etc/elixir_tzdata_data /etc/yt-dlp/plugins && \
    ln -sfn /data /config && \
    ln -sfn /media/youtube /downloads

# Default env (can be overridden by add-on config)
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    MIX_ENV=prod RUN_CONTEXT=selfhosted UMASK=022 PORT=8945 \
    PHX_SERVER=true

# Patch the release env so SECRET_KEY_BASE is guaranteed before runtime.exs
# (read from /config/secret_key_base or generate once and persist)
RUN set -eux; \
  for f in /app/releases/*/env.sh; do \
    { \
      echo ''; \
      echo '# --- HA add-on env patch ---'; \
      echo 'set -eu'; \
      echo 'if [ -z "${SECRET_KEY_BASE:-}" ]; then'; \
      echo '  if [ -f /config/secret_key_base ]; then'; \
      echo '    export SECRET_KEY_BASE="$(cat /config/secret_key_base)";'; \
      echo '  else'; \
      echo '    if command -v openssl >/dev/null 2>&1; then'; \
      echo '      SECRET="$(openssl rand -base64 64 | tr -d "/=+\n" | head -c 64)";'; \
      echo '    else'; \
      echo '      SECRET="$(tr -dc "A-Za-z0-9" </dev/urandom | head -c 64)";'; \
      echo '    fi'; \
      echo '    printf "%s" "$SECRET" > /config/secret_key_base'; \
      echo '    chmod 600 /config/secret_key_base || true'; \
      echo '    export SECRET_KEY_BASE="$SECRET"'; \
      echo '  fi'; \
      echo 'fi'; \
      echo '# Force UTF-8 filenames if host encoding is latin1'; \
      echo 'export ELIXIR_ERL_OPTIONS="${ELIXIR_ERL_OPTIONS:+$ELIXIR_ERL_OPTIONS }+fnu"'; \
      echo '# --- end patch ---'; \
    } >> "$f"; \
  done

# s6-overlay service: prepare init + supervised run
RUN set -eux; \
  mkdir -p /etc/cont-init.d /etc/services.d/pinchflat; \
  # cont-init script (runs before services)
  printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'DOWNLOADS_DIR="${DOWNLOADS_DIR:-/media/YouTube}"' \
    'mkdir -p "$DOWNLOADS_DIR"' \
    'ln -sfn "$DOWNLOADS_DIR" /downloads' \
  > /etc/cont-init.d/10-setup; \
  chmod +x /etc/cont-init.d/10-setup; \
  # long-running service
  printf '%s\n' \
    '#!/bin/sh' \
    'exec /app/bin/pinchflat start' \
  > /etc/services.d/pinchflat/run; \
  chmod +x /etc/services.d/pinchflat/run

EXPOSE 8945
HEALTHCHECK --interval=30s --start-period=20s \
  CMD curl -fsS http://127.0.0.1:${PORT}/healthcheck || exit 1
