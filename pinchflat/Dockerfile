# syntax=docker/dockerfile:1

##### Global (must be before any FROM using it) #####
ARG BUILD_FROM

# Builder versions (Debian toolchain)
ARG ELIXIR_VERSION=1.18.4
ARG OTP_VERSION=27.2.4
ARG DEBIAN_VERSION=bookworm-20250428-slim
ARG PINCHFLAT_REF=master   # set to a tag like v2025.6.6 for reproducibility

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

# FFmpeg/ffprobe (prebuilt, arch-aware)
RUN set -eux; \
  case "${TARGETPLATFORM:-linux/amd64}" in \
    "linux/arm64") FFMPEG_URL="https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linuxarm64-gpl.tar.xz" ;; \
    *)             FFMPEG_URL="https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linux64-gpl.tar.xz" ;; \
  esac; \
  curl -L "$FFMPEG_URL" -o /tmp/ffmpeg.tar.xz && \
  tar -xf /tmp/ffmpeg.tar.xz --strip-components=2 --no-anchored -C /usr/local/bin ffmpeg ffprobe && \
  rm -f /tmp/ffmpeg.tar.xz

# Fetch source and build release
WORKDIR /src
RUN git clone --depth 1 --branch "${PINCHFLAT_REF}" https://github.com/kieraneglin/pinchflat ./

RUN yarn --cwd assets install && \
    mix deps.get --only prod && \
    mix deps.compile && \
    mix assets.deploy && \
    mix compile && \
    mix release

##### -------- Runtime (Home Assistant add-on base: Debian) -------- #####
FROM $BUILD_FROM AS runner
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

# Minimal runtime deps (keep slim for memory)
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      libstdc++6 openssl libncurses5 locales ca-certificates \
      python3 curl tzdata && \
    update-ca-certificates && \
    sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen && \
    curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Use the Debian-built ffmpeg/ffprobe from builder
COPY --from=builder /usr/local/bin/ffmpeg /usr/bin/ffmpeg
COPY --from=builder /usr/local/bin/ffprobe /usr/bin/ffprobe

# App release
WORKDIR /app
COPY --from=builder /src/_build/prod/rel/pinchflat ./

# Persist & paths for HA
RUN mkdir -p /data /media/YouTube /etc/elixir_tzdata_data /etc/yt-dlp/plugins && \
    ln -sfn /data /config && \
    ln -sfn /media/YouTube /downloads

# Default env (memory tuning + UTF-8)
ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    MIX_ENV=prod \
    RUN_CONTEXT=selfhosted \
    UMASK=022 \
    PORT=8945 \
    PHX_SERVER=true \
    ELIXIR_ERL_OPTIONS="+S 2:2 +fnu" \
    ERL_AFLAGS="-emu_type emu" \
    MALLOC_ARENA_MAX=2

# Ensure SECRET_KEY_BASE exists *before* runtime.exs via release env.sh
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
      echo '# Force UTF-8 filenames'; \
      echo 'export ELIXIR_ERL_OPTIONS="${ELIXIR_ERL_OPTIONS:+$ELIXIR_ERL_OPTIONS }+fnu"'; \
      echo '# --- end patch ---'; \
    } >> "$f"; \
  done

# s6 init scripts
RUN set -eux; \
  mkdir -p /etc/cont-init.d /etc/services.d/pinchflat; \
  printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'DOWNLOADS_DIR="${DOWNLOADS_DIR:-/media/YouTube}"' \
    'mkdir -p "$DOWNLOADS_DIR"' \
    'ln -sfn "$DOWNLOADS_DIR" /downloads' \
  > /etc/cont-init.d/10-setup; \
  chmod +x /etc/cont-init.d/10-setup; \
  printf '%s\n' \
    '#!/bin/sh' \
    'exec /app/bin/pinchflat start' \
  > /etc/services.d/pinchflat/run; \
  chmod +x /etc/services.d/pinchflat/run

EXPOSE 8945
HEALTHCHECK --interval=30s --start-period=20s \
  CMD curl -fsS http://127.0.0.1:${PORT}/healthcheck || exit 1
