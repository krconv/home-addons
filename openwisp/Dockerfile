ARG BUILD_FROM

FROM openwisp/openwisp-dashboard:25.10.1 AS upstream_lock
RUN python -m pip freeze > /tmp/requirements.lock

FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

ARG OPENWISP_DOCKER_REPO=https://github.com/openwisp/docker-openwisp.git
ARG OPENWISP_DOCKER_REF=25.10.1

ARG DJANGO_SOURCE="django~=5.2.0"
ARG OPENWISP_CONTROLLER_SOURCE="openwisp-controller~=1.2.0"
ARG OPENWISP_USERS_SOURCE="openwisp-users~=1.2.0"
ARG OPENWISP_UTILS_SOURCE="openwisp-utils[celery,channels]~=1.2.0"
ARG OPENWISP_NOTIFICATIONS_SOURCE="openwisp-notifications~=1.2.0"
ARG OPENWISP_IPAM_SOURCE="openwisp-ipam~=1.2.0"
ARG OPENWISP_MONITORING_SOURCE="openwisp-monitoring~=1.2.0"
ARG OPENWISP_FIRMWARE_SOURCE="openwisp-firmware-upgrader~=1.2.0"
ARG OPENWISP_TOPOLOGY_SOURCE="openwisp-network-topology~=1.2.0"
ARG OPENWISP_RADIUS_SOURCE="openwisp-radius~=1.2.0"

# Pin Python packages to upstream openwisp/openwisp-dashboard:25.10.1.
COPY --from=upstream_lock /tmp/requirements.lock /tmp/requirements.lock

# System dependencies for OpenWISP core (GIS, Postgres, uWSGI, nginx)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    bash \ # local shell helper (not upstream)
    ca-certificates \ # upstream base
    curl \ # local clone helper (not upstream)
    git \ # local clone helper (not upstream)
    gcc \ # upstream base build deps
    g++ \ # upstream base build deps
    make \ # local build deps (not upstream)
    python3 \ # upstream base
    python3-dev \ # upstream base build deps
    python3-pip \ # upstream base build deps
    libpython3.11 \ # HA base only (uwsgi shared lib)
    libffi-dev \ # upstream base build deps
    libssl-dev \ # local build deps (not upstream)
    libpq-dev \ # upstream base build deps
    zlib1g-dev \ # upstream base build deps
    libjpeg-dev \ # upstream base build deps
    tzdata \ # upstream base
    gdal-bin \ # upstream base
    libgdal-dev \ # local build deps (not upstream)
    libgeos-dev \ # local build deps (not upstream)
    proj-bin \ # local build deps (not upstream)
    libproj-dev \ # local build deps (not upstream)
    sqlite3 \ # local build deps (not upstream)
    libsqlite3-dev \ # local build deps (not upstream)
    libxml2-dev \ # upstream base build deps
    libxslt-dev \ # upstream base build deps
    gettext \ # upstream base
    libcairo2 \ # upstream base
    libpango-1.0-0 \ # upstream base (weasyprint)
    libpangoft2-1.0-0 \ # upstream base (weasyprint)
    nginx \ # upstream nginx image (merged here)
    openssh-client \ # upstream base
    openssl && \ # upstream nginx image (merged here)
    if ! getent group nginx >/dev/null 2>&1; then addgroup --system nginx; fi && \
    if ! id -u nginx >/dev/null 2>&1; then \
      adduser --system --ingroup nginx --no-create-home --shell /usr/sbin/nologin nginx; \
    fi && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/openwisp

RUN git clone --depth 1 --branch "${OPENWISP_DOCKER_REF}" "${OPENWISP_DOCKER_REPO}" /tmp/docker-openwisp && \
    python3 -m pip install --upgrade pip && \
    python3 -m pip install -c /tmp/requirements.lock "${DJANGO_SOURCE}" && \
    python3 -m pip install -c /tmp/requirements.lock -r /tmp/docker-openwisp/images/openwisp_base/requirements.txt && \
    python3 -m pip install -c /tmp/requirements.lock \
      "${OPENWISP_CONTROLLER_SOURCE}" \
      "${OPENWISP_USERS_SOURCE}" \
      "${OPENWISP_UTILS_SOURCE}" \
      "${OPENWISP_NOTIFICATIONS_SOURCE}" \
      "${OPENWISP_IPAM_SOURCE}" \
      "${OPENWISP_MONITORING_SOURCE}" \
      "${OPENWISP_FIRMWARE_SOURCE}" \
      "${OPENWISP_TOPOLOGY_SOURCE}" \
      "${OPENWISP_RADIUS_SOURCE}" \
      django-nested-admin \
      django-private-storage \
      dj-rest-auth && \
    python3 -m pip install -c /tmp/requirements.lock -r /tmp/docker-openwisp/images/openwisp_nginx/requirements.txt && \
    cp -r /tmp/docker-openwisp/images/common/* /opt/openwisp/ && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/load_init_data.py /opt/openwisp/ && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/openvpn.json /opt/openwisp/ && \
    cp -r /tmp/docker-openwisp/images/openwisp_nginx/* /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/init_command.sh /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/utils.sh /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/services.py /etc/nginx/ && \
    rm -rf /tmp/docker-openwisp && \
    rm -rf /var/lib/apt/lists/* /root/.cache/pip /tmp/*

COPY rootfs/ /
