# syntax=docker/dockerfile:1

##### Global args (needed before any FROM) #####
# Provided by HA Supervisor from build.yaml (do not hardcode here)
ARG BUILD_FROM

# Builder versions
ARG ELIXIR_VERSION=1.18.4
ARG OTP_VERSION=27.2.4
ARG DEBIAN_VERSION=bookworm-20250428-slim

# Pinchflat git ref to build. Use a tag (recommended) or 'master'.
ARG PINCHFLAT_REF=master

##### -------- Builder stage (Debian toolchain) -------- #####
FROM hexpm/elixir:${ELIXIR_VERSION}-erlang-${OTP_VERSION}-debian-${DEBIAN_VERSION} AS builder
ARG TARGETPLATFORM
ARG PINCHFLAT_REF

RUN echo "Building for ${TARGETPLATFORM:-linux/amd64}, source ref: ${PINCHFLAT_REF}"

# Base tools, Node/Yarn, Hex/Rebar, FFmpeg/ffprobe for static runtime copy
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      build-essential git curl ca-certificates xz-utils && \
    curl -sL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup.sh && \
    bash /tmp/nodesource_setup.sh && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g yarn && \
    mix local.hex --force && mix local.rebar --force && \
    rm -rf /var/lib/apt/lists/*

# FFmpeg/ffprobe (prebuilt from yt-dlp builds; arch-aware)
RUN set -eux; \
  case "${TARGETPLATFORM:-linux/amd64}" in \
    "linux/arm64") FFMPEG_URL="https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linuxarm64-gpl.tar.xz" ;; \
    *)             FFMPEG_URL="https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linux64-gpl.tar.xz" ;; \
  esac; \
  curl -L "$FFMPEG_URL" -o /tmp/ffmpeg.tar.xz && \
  tar -xf /tmp/ffmpeg.tar.xz --strip-components=2 --no-anchored -C /usr/local/bin ffmpeg && \
  tar -xf /tmp/ffmpeg.tar.xz --strip-components=2 --no-anchored -C /usr/local/bin ffprobe && \
  rm -f /tmp/ffmpeg.tar.xz

# Fetch Pinchflat source
WORKDIR /src
RUN git clone --depth 1 --branch "${PINCHFLAT_REF}" https://github.com/kieraneglin/pinchflat ./

# Build Pinchflat (assets + release)
ENV MIX_ENV=prod ERL_FLAGS="+JPperf true"
RUN yarn --cwd assets install && \
    mix deps.get --only prod && \
    mix deps.compile && \
    mix assets.deploy && \
    mix compile && \
    mix release

##### -------- Runtime stage (Home Assistant add-on base) -------- #####
FROM $BUILD_FROM AS runner

# Optional: these are set by Supervisor via --build-arg too
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

# Runtime packages + yt-dlp + apprise + locales
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      libstdc++6 openssl libncurses5 locales ca-certificates \
      python3 python3-mutagen pipx curl zip openssh-client jq procps tzdata xz-utils && \
    update-ca-certificates && \
    PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install apprise && \
    curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp && \
    sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy ffmpeg/ffprobe built in the builder
COPY --from=builder /usr/local/bin/ffmpeg /usr/bin/ffmpeg
COPY --from=builder /usr/local/bin/ffprobe /usr/bin/ffprobe

# Copy Pinchflat release
WORKDIR /app
COPY --from=builder /src/_build/prod/rel/pinchflat ./

# HA-friendly paths: /data persists; /media is HA's media share (mapped via config.yaml)
# Symlink for Pinchflat's expectations without changing the app
RUN mkdir -p /data /media/youtube /etc/elixir_tzdata_data /etc/yt-dlp/plugins && \
    ln -sfn /data /config && \
    ln -sfn /media/youtube /downloads && \
    chmod -R ugo+rw /etc/elixir_tzdata_data /etc/yt-dlp /etc/yt-dlp/plugins

ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    MIX_ENV=prod \
    RUN_CONTEXT=selfhosted \
    UMASK=022 \
    PORT=8945

EXPOSE 8945

HEALTHCHECK --interval=30s --start-period=15s \
  CMD curl --fail http://localhost:${PORT}/healthcheck || exit 1

# Start: ensure /downloads points to mapped media (in case env overrides), then run release script
CMD ["/bin/sh", "-c", "mkdir -p ${DOWNLOADS_DIR:-/media/youtube} && ln -sfn ${DOWNLOADS_DIR:-/media/youtube} /downloads && exec /app/bin/docker_start"]
