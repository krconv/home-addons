# syntax=docker/dockerfile:1
ARG BUILD_FROM

# ---------- Build stage (unchanged: Debian toolchain) ----------
ARG ELIXIR_VERSION=1.18.4
ARG OTP_VERSION=27.2.4
ARG DEBIAN_VERSION=bookworm-20250428-slim
ARG BUILDER_IMAGE="hexpm/elixir:${ELIXIR_VERSION}-erlang-${OTP_VERSION}-debian-${DEBIAN_VERSION}"

FROM ${BUILDER_IMAGE} AS builder
ARG TARGETPLATFORM
RUN echo "Building for ${TARGETPLATFORM:?}"

# Build deps & ffmpeg/ffprobe (same as your original)
RUN apt-get update -y && \
    apt-get install -y build-essential git curl && \
    curl -sL https://deb.nodesource.com/setup_20.x -o nodesource_setup.sh && \
    bash nodesource_setup.sh && \
    apt-get install -y nodejs && npm install -g yarn && \
    mix local.hex --force && mix local.rebar --force && \
    export FFMPEG_DOWNLOAD=$(case ${TARGETPLATFORM:-linux/amd64} in \
      "linux/amd64") echo "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linux64-gpl.tar.xz" ;; \
      "linux/arm64") echo "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/autobuild-2024-07-30-14-10/ffmpeg-N-116468-g0e09f6d690-linuxarm64-gpl.tar.xz" ;; \
      *) echo "" ;; esac) && \
    curl -L ${FFMPEG_DOWNLOAD} --output /tmp/ffmpeg.tar.xz && \
    tar -xf /tmp/ffmpeg.tar.xz --strip-components=2 --no-anchored -C /usr/local/bin/ ffmpeg && \
    tar -xf /tmp/ffmpeg.tar.xz --strip-components=2 --no-anchored -C /usr/local/bin/ ffprobe && \
    apt-get clean && rm -f /var/lib/apt/lists/*_*

WORKDIR /app
ENV MIX_ENV="prod" ERL_FLAGS="+JPperf true"

COPY mix.exs mix.lock ./
RUN mix deps.get --only $MIX_ENV && mkdir config

COPY config/config.exs config/${MIX_ENV}.exs config/
RUN mix deps.compile

COPY priv priv
COPY lib lib
COPY assets assets

RUN yarn --cwd assets install && mix assets.deploy && mix compile
COPY config/runtime.exs config/
RUN mix release

# ---------- Runtime stage (HA add-on base image: Debian) ----------
ARG BUILD_VERSION
ARG BUILD_ARCH
FROM $BUILD_FROM AS runner

# Required HA add-on labels
LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  io.hass.arch="${BUILD_ARCH}"

ARG PORT=8945

# ffmpeg/ffprobe from builder
COPY --from=builder /usr/local/bin/ffmpeg /usr/bin/ffmpeg
COPY --from=builder /usr/local/bin/ffprobe /usr/bin/ffprobe

# Runtime packages + yt-dlp + apprise
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      libstdc++6 openssl libncurses5 locales ca-certificates \
      python3 python3-mutagen pipx curl zip openssh-client jq procps tzdata && \
    update-ca-certificates && \
    PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install apprise && \
    curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp && \
    sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8

WORKDIR /app

# Pinchflat release from builder
COPY --from=builder /app/_build/prod/rel/pinchflat ./

# Prepare HA-friendly paths:
# /data is the add-on's persistent storage. Symlink /config and /downloads to HA mounts.
RUN mkdir -p /data /media/youtube /etc/elixir_tzdata_data /etc/yt-dlp/plugins && \
    ln -sfn /data /config && \
    ln -sfn /media/youtube /downloads && \
    chmod -R ugo+rw /etc/elixir_tzdata_data /etc/yt-dlp /etc/yt-dlp/plugins

ENV MIX_ENV="prod" PORT=${PORT} RUN_CONTEXT="selfhosted" UMASK=022

# Healthcheck (Supervisor also has a watchdog)
HEALTHCHECK --interval=30s --start-period=15s \
  CMD curl --fail http://localhost:${PORT}/healthcheck || exit 1

# Start via simple run script (no S6 services needed)
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
