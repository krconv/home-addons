ARG BUILD_FROM
FROM $BUILD_FROM

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

ARG POSTGRES_MAJOR=17
ARG POSTGRES_VERSION=17.7
ARG POSTGRES_PKG_VERSION=17.7-3.pgdg12+1
ARG POSTGIS_PKG_VERSION=3.6.1+dfsg-1.pgdg12+1

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    POSTGRES_VERSION=${POSTGRES_VERSION} \
    PGDATA=/data/pgdata

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates gnupg wget && \
    echo "deb http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo ${VERSION_CODENAME})-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list && \
    wget -qO- https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
      gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      postgresql-${POSTGRES_MAJOR}=${POSTGRES_PKG_VERSION} \
      postgresql-client-${POSTGRES_MAJOR}=${POSTGRES_PKG_VERSION} \
      postgresql-${POSTGRES_MAJOR}-postgis-3=${POSTGIS_PKG_VERSION} \
      postgresql-${POSTGRES_MAJOR}-postgis-3-scripts=${POSTGIS_PKG_VERSION} && \
    apt-get purge -y --auto-remove wget gnupg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
