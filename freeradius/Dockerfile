ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      freeradius \
      freeradius-rest \
      jq && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
