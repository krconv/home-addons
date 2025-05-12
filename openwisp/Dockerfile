ARG BUILD_FROM
FROM $BUILD_FROM

# Install required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    build-essential libpq-dev libjpeg-dev libffi-dev \
    libxml2-dev libxslt1-dev zlib1g-dev g++ \
    libcairo2 libpangocairo-1.0-0 \
    gdal-bin gettext fping openssh-client \
    supervisor nginx postgresql postgresql-contrib \
    postgis postgresql-postgis redis-server \
    openssl git procps wget curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create openwisp user
RUN useradd --system --password '' --create-home --shell /bin/bash \
    --gid root --uid 1001 openwisp

# Set up directory structure
RUN mkdir -p /opt/openwisp/static \
    /opt/openwisp/media \
    /opt/openwisp/private \
    /opt/openwisp/logs \
    /home/openwisp/.ssh \
    && chown -R openwisp:root /opt/openwisp \
    && chown -R openwisp:root /home/openwisp/.ssh

# Install Python dependencies
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel

# Install OpenWISP packages
RUN pip3 install --no-cache-dir \
    openwisp-controller~=1.1.0 \
    openwisp-monitoring~=1.1.0 \
    openwisp-firmware-upgrader~=1.1.0 \
    openwisp-network-topology~=1.1.1 \
    openwisp-radius~=1.1.0 \
    django~=4.2.0 \
    psycopg2-binary \
    gunicorn \
    supervisor

# Copy common files from reference repo
COPY reference/docker-openwisp-master/images/common /opt/openwisp/
# Copy configuration and scripts
COPY rootfs /

# Set up PostgreSQL
RUN mkdir -p /var/run/postgresql && chown -R postgres:postgres /var/run/postgresql && \
    mkdir -p /var/lib/postgresql/data && chown -R postgres:postgres /var/lib/postgresql/data

# Set up Redis
RUN mkdir -p /var/lib/redis && chown -R redis:redis /var/lib/redis

# Set up Nginx
RUN mkdir -p /var/log/nginx && chown -R www-data:www-data /var/log/nginx

# Add FreeRADIUS setup
COPY reference/docker-openwisp-master/images/openwisp_freeradius/raddb /etc/freeradius

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    TZ=UTC \
    DEBUG_MODE=False \
    REDIS_HOST=localhost \
    REDIS_PORT=6379 \
    REDIS_PASS= \
    DB_ENGINE=django.contrib.gis.db.backends.postgis \
    DB_NAME=openwisp_db \
    DB_USER=openwisp \
    DB_PASS=openwisp \
    DB_HOST=localhost \
    DB_PORT=5432 \
    DB_SSLMODE=disable \
    EMAIL_BACKEND=djcelery_email.backends.CeleryEmailBackend \
    EMAIL_HOST=localhost \
    EMAIL_HOST_PORT=25 \
    EMAIL_HOST_USER="" \
    EMAIL_HOST_PASSWORD="" \
    EMAIL_HOST_TLS=False \
    EMAIL_TIMEOUT=10 \
    EMAIL_DJANGO_DEFAULT=example@example.org \
    DJANGO_LOG_LEVEL=ERROR \
    DJANGO_LANGUAGE_CODE=en-gb \
    DJANGO_SECRET_KEY=default_home_assistant_key \
    DASHBOARD_APP_PORT=8000 \
    API_APP_PORT=8001 \
    WEBSOCKET_APP_PORT=8002 \
    DASHBOARD_INTERNAL=dashboard.internal \
    API_INTERNAL=api.internal \
    USE_OPENWISP_RADIUS=True \
    USE_OPENWISP_TOPOLOGY=True \
    USE_OPENWISP_FIRMWARE=True \
    USE_OPENWISP_MONITORING=True \
    USE_OPENWISP_CELERY_TASK_ROUTES_DEFAULTS=True

# Make scripts executable
RUN chmod +x /app/run.sh

EXPOSE 80 443 8000 8001 8002 1812/udp 1813/udp 1194/udp
WORKDIR /opt/openwisp

CMD [ "/app/run.sh" ]