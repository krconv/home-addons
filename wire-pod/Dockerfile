# Upstream only publishes `main`/`nightly` image tags (no per-release tags
# despite tagged GitHub releases existing), so we pin by digest for
# reproducibility. This is `main` as of commit 347c45f (2026-08-27).
FROM ghcr.io/kercre123/wire-pod@sha256:c3c52a0f27227caf407db4e24f199a1c35284ebde6b21d1e56d8e2f75e456b9a

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

RUN apt-get update && \
    apt-get install -y --no-install-recommends jq && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT ["/run.sh"]
CMD ["/opt/wire-pod/docker/entrypoint.sh", "/opt/wire-pod/chipper/start.sh"]
