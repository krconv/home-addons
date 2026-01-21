ARG BUILD_FROM
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

# Upstream-aligned defaults for build-time collectstatic.
ENV DASHBOARD_APP_SERVICE=dashboard \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    DEBUG_MODE=False \
    REDIS_HOST=redis \
    REDIS_PORT=6379 \
    REDIS_PASS= \
    DB_ENGINE=django.contrib.gis.db.backends.postgis \
    DB_NAME=openwisp_db \
    DB_USER=admin \
    DB_PASS=admin \
    DB_HOST=postgres \
    DB_PORT=5432 \
    DB_SSLMODE=disable \
    DB_SSLKEY=None \
    DB_SSLCERT=None \
    DB_SSLROOTCERT=None \
    DB_OPTIONS={} \
    INFLUXDB_USER=admin \
    INFLUXDB_PASS=admin \
    INFLUXDB_NAME=openwisp \
    INFLUXDB_HOST=influxdb \
    INFLUXDB_PORT=8086 \
    INFLUXDB_DEFAULT_RETENTION_POLICY=26280h0m0s \
    EMAIL_BACKEND=djcelery_email.backends.CeleryEmailBackend \
    EMAIL_HOST=postfix \
    EMAIL_HOST_PORT=25 \
    EMAIL_HOST_USER="" \
    EMAIL_HOST_PASSWORD="" \
    EMAIL_HOST_TLS=False \
    EMAIL_TIMEOUT=10 \
    EMAIL_DJANGO_DEFAULT=example@example.org \
    DJANGO_LOG_LEVEL=ERROR \
    DJANGO_LANGUAGE_CODE=en-gb \
    OPENWISP_RADIUS_FREERADIUS_ALLOWED_HOSTS=172.18.0.0/16 \
    DJANGO_X509_DEFAULT_CERT_VALIDITY=1825 \
    DJANGO_X509_DEFAULT_CA_VALIDITY=3650 \
    DJANGO_SECRET_KEY=DEFAULT_BAD_KEY \
    DJANGO_CORS_HOSTS=http://localhost \
    DJANGO_SENTRY_DSN="" \
    DJANGO_LEAFET_CENTER_X_AXIS=0 \
    DJANGO_LEAFET_CENTER_Y_AXIS=0 \
    DJANGO_LEAFET_ZOOM=1 \
    NGINX_CLIENT_BODY_SIZE=30 \
    DASHBOARD_APP_PORT=8000 \
    API_APP_PORT=8001 \
    WEBSOCKET_APP_PORT=8002 \
    DASHBOARD_INTERNAL=dashboard.internal \
    API_INTERNAL=api.internal \
    USE_OPENWISP_RADIUS=True \
    USE_OPENWISP_TOPOLOGY=True \
    USE_OPENWISP_FIRMWARE=True \
    USE_OPENWISP_MONITORING=True \
    USE_OPENWISP_CELERY_TASK_ROUTES_DEFAULTS=True \
    METRIC_COLLECTION=True \
    SSL_CERT_MODE=Yes \
    NGINX_HTTP2=http2 \
    NGINX_ADMIN_ALLOW_NETWORK=all \
    NGINX_HTTPS_ALLOWED_IPS=all \
    NGINX_HTTP_ALLOW=False \
    NGINX_SERVER_NAME_HASH_BUCKET=32 \
    NGINX_SSL_CONFIG='' \
    NGINX_IP6_STRING='' \
    NGINX_IP6_80_STRING='' \
    NGINX_80_CONFIG='' \
    NGINX_GZIP_SWITCH=on \
    NGINX_GZIP_LEVEL=6 \
    NGINX_GZIP_PROXIED=any \
    NGINX_GZIP_MIN_LENGTH=1000 \
    NGINX_GZIP_TYPES='text/plain image/svg+xml application/json application/javascript text/xml text/css application/xml application/x-font-ttf font/opentype' \
    NGINX_CUSTOM_FILE=False \
    NINGX_REAL_REMOTE_ADDR='$real_ip' \
    NGINX_SSL_PORT=443 \
    DASHBOARD_DOMAIN=dashboard.example.com \
    API_DOMAIN=api.example.com \
    DJANGO_SETTINGS_MODULE=openwisp.settings_dashboard

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
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libharfbuzz0b \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    nginx \
    openssh-client \
    openssl && \
    if ! getent group nginx >/dev/null 2>&1; then addgroup --system nginx; fi && \
    if ! id -u nginx >/dev/null 2>&1; then \
      adduser --system --ingroup nginx --no-create-home --shell /usr/sbin/nologin nginx; \
    fi && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/openwisp

RUN git clone --depth 1 --branch "${OPENWISP_DOCKER_REF}" "${OPENWISP_DOCKER_REPO}" /tmp/docker-openwisp && \
    python3 -m pip install --upgrade pip && \
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
    python3 -m pip install -r /tmp/docker-openwisp/images/openwisp_nginx/requirements.txt && \
    cp -r /tmp/docker-openwisp/images/common/* /opt/openwisp/ && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/load_init_data.py /opt/openwisp/ && \
    cp /tmp/docker-openwisp/images/openwisp_dashboard/openvpn.json /opt/openwisp/ && \
    cp -r /tmp/docker-openwisp/images/openwisp_nginx/* /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/init_command.sh /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/utils.sh /etc/nginx/ && \
    cp /tmp/docker-openwisp/images/common/services.py /etc/nginx/ && \
    rm -rf /tmp/docker-openwisp

RUN mkdir -p /opt/openwisp/static /opt/openwisp/media /opt/openwisp/private /opt/openwisp/logs && \
    ln -s /opt/openwisp/openwisp /opt/openwisp/openwisp2

COPY rootfs/ /

RUN python3 /opt/openwisp/collectstatic.py
