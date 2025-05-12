#!/usr/bin/with-contenv bashio

# Set up environment variables from config
export SITE_NAME=$(bashio::config 'site_name')
export SITE_URL=$(bashio::config 'site_url')
export ADMIN_EMAIL=$(bashio::config 'admin_email')
export ADMIN_PASSWORD=$(bashio::config 'admin_password')
export TZ=$(bashio::config 'time_zone')
export DJANGO_LANGUAGE_CODE=$(bashio::config 'language_code')
export DEBUG_MODE=$(bashio::config 'debug')

bashio::log.info "Starting OpenWISP..."

# Create required directories
mkdir -p /data/postgres
mkdir -p /data/redis
mkdir -p /data/media
mkdir -p /data/static
mkdir -p /data/private

# Set correct permissions
chown -R postgres:postgres /data/postgres
chown -R redis:redis /data/redis
chown -R openwisp:root /data/media
chown -R openwisp:root /data/static
chown -R openwisp:root /data/private

# Initialize PostgreSQL database if it doesn't exist
if [ ! -f "/data/postgres/PG_VERSION" ]; then
    bashio::log.info "Initializing PostgreSQL database..."
    su - postgres -c "initdb -D /data/postgres"
    
    # Update PostgreSQL configuration
    echo "listen_addresses = '*'" >> /data/postgres/postgresql.conf
    echo "host all all 0.0.0.0/0 md5" >> /data/postgres/pg_hba.conf
    
    # Start PostgreSQL
    su - postgres -c "pg_ctl start -D /data/postgres"
    
    # Wait for PostgreSQL to start
    until su - postgres -c "pg_isready"; do
        bashio::log.info "Waiting for PostgreSQL to start..."
        sleep 1
    done
    
    # Create database and user
    su - postgres -c "createuser -s $DB_USER"
    su - postgres -c "psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';\""
    su - postgres -c "createdb -O $DB_USER $DB_NAME"
    su - postgres -c "psql -d $DB_NAME -c 'CREATE EXTENSION IF NOT EXISTS postgis;'"
    su - postgres -c "psql -d $DB_NAME -c 'CREATE EXTENSION IF NOT EXISTS postgis_topology;'"
    
    # Stop PostgreSQL (will be started by supervisord)
    su - postgres -c "pg_ctl stop -D /data/postgres"
else
    bashio::log.info "PostgreSQL database already exists"
fi

# Link data directories
ln -sf /data/media /opt/openwisp/media
ln -sf /data/static /opt/openwisp/static
ln -sf /data/private /opt/openwisp/private

# Generate initial Django secret key if needed
if [ ! -f "/data/secret_key.txt" ]; then
    bashio::log.info "Generating Django secret key..."
    python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())" > /data/secret_key.txt
fi
export DJANGO_SECRET_KEY=$(cat /data/secret_key.txt)

# Initialize SSL certificates if needed
if [ ! -d "/ssl/openwisp" ]; then
    bashio::log.info "Generating SSL certificates..."
    mkdir -p /ssl/openwisp
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /ssl/openwisp/privkey.pem \
        -out /ssl/openwisp/fullchain.pem \
        -subj "/CN=openwisp.local" \
        -addext "subjectAltName=DNS:openwisp.local,DNS:localhost,IP:127.0.0.1"
fi

# Start all services with supervisord
bashio::log.info "Starting all OpenWISP services..."
exec supervisord -c /etc/supervisor/supervisord.conf -n