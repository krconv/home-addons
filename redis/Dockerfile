ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

ARG REDIS_VERSION=8.4.0
ARG REDIS_PKG_VERSION=6:8.4.0-1rl1~bookworm1

ENV REDIS_VERSION=${REDIS_VERSION}

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      gnupg \
      wget && \
    echo "deb https://packages.redis.io/deb bookworm main" \
      > /etc/apt/sources.list.d/redis.list && \
    wget -qO- https://packages.redis.io/gpg | \
      gpg --dearmor -o /etc/apt/trusted.gpg.d/redis.gpg && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      gosu \
      jq \
      redis-server=${REDIS_PKG_VERSION} \
      redis-tools=${REDIS_PKG_VERSION} && \
    if ! getent group redis >/dev/null 2>&1; then addgroup --system redis; fi && \
    if ! id -u redis >/dev/null 2>&1; then \
      adduser --system --ingroup redis --home /data --shell /usr/sbin/nologin redis; \
    fi && \
    apt-get purge -y --auto-remove wget gnupg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
