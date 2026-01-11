ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

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

# System dependencies for OpenWISP core (GIS, Postgres, uWSGI, nginx)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    gcc \
    g++ \
    make \
    python3 \
    python3-dev \
    python3-pip \
    libffi-dev \
    libssl-dev \
    libpq-dev \
    zlib1g-dev \
    libjpeg-dev \
    tzdata \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    proj-bin \
    libproj-dev \
    sqlite3 \
    libsqlite3-dev \
    libxml2-dev \
    libxslt-dev \
    gettext \
    nginx \
    openssh-client \
    openssl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/openwisp

RUN git clone --depth 1 --branch "${OPENWISP_DOCKER_REF}" "${OPENWISP_DOCKER_REPO}" /tmp/docker-openwisp

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install "${DJANGO_SOURCE}" && \
    python3 -m pip install -r /tmp/docker-openwisp/images/openwisp_base/requirements.txt && \
    python3 -m pip install \
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
    python3 -m pip install -r /tmp/docker-openwisp/images/openwisp_nginx/requirements.txt

# OpenWISP runtime project (from docker-openwisp common)
RUN cp -r /tmp/docker-openwisp/images/common/* /opt/openwisp/ && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/module_settings.py /opt/openwisp/openwisp/module_settings.py && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/urls.py /opt/openwisp/openwisp/urls.py && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/load_init_data.py /opt/openwisp/ && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/openvpn.json /opt/openwisp/ && \
    cp -r /tmp/docker-openwisp/images/openwisp_nginx/* /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/init_command.sh /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/utils.sh /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/services.py /etc/nginx/ && \
    rm -rf /tmp/docker-openwisp

COPY rootfs/ /

EXPOSE 80
