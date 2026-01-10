ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ARG OPENWISP_DOCKER_REPO=https://github.com/openwisp/docker-openwisp.git
ARG OPENWISP_DOCKER_REF=master

ARG DJANGO_SOURCE=django
ARG OPENWISP_CONTROLLER_SOURCE=openwisp-controller
ARG OPENWISP_USERS_SOURCE=openwisp-users
ARG OPENWISP_UTILS_SOURCE=openwisp-utils[celery,channels]
ARG OPENWISP_NOTIFICATIONS_SOURCE=openwisp-notifications
ARG OPENWISP_IPAM_SOURCE=openwisp-ipam

# System dependencies for OpenWISP core (GIS, Postgres, uWSGI, nginx)
RUN apk add --no-cache \
    bash \
    ca-certificates \
    curl \
    git \
    gcc \
    g++ \
    make \
    musl-dev \
    python3-dev \
    libffi-dev \
    openssl-dev \
    postgresql-dev \
    zlib-dev \
    jpeg-dev \
    tzdata \
    gdal \
    geos \
    proj \
    proj-util \
    sqlite \
    sqlite-dev \
    libxml2-dev \
    libxslt-dev \
    gettext \
    nginx \
    openssh-client \
    openssl

WORKDIR /opt/openwisp

RUN git clone --depth 1 --branch "${OPENWISP_DOCKER_REF}" "${OPENWISP_DOCKER_REPO}" /tmp/docker-openwisp

RUN pip install --upgrade pip && \
    pip install "${DJANGO_SOURCE}" && \
    pip install -r /tmp/docker-openwisp/images/openwisp_base/requirements.txt && \
    pip install "${OPENWISP_CONTROLLER_SOURCE}" \
      "${OPENWISP_USERS_SOURCE}" \
      "${OPENWISP_UTILS_SOURCE}" \
      "${OPENWISP_NOTIFICATIONS_SOURCE}" \
      "${OPENWISP_IPAM_SOURCE}" \
      django-nested-admin \
      django-private-storage \
      dj-rest-auth && \
    pip install -r /tmp/docker-openwisp/images/openwisp_nginx/requirements.txt

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
