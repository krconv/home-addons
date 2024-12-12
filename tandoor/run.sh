#!/usr/bin/with-contenv bashio

if [ ! -f /data/secret.txt ]; then
    bashio::log.info "Generating secret key..."
    mkdir /data
    head /dev/urandom | tr -dc A-Za-z0-9 | head -c 50 > /data/secret.txt
fi
export SECRET_KEY_FILE=/data/secret.txt

export DB_ENGINE=django.db.backends.sqlite3
export POSTGRES_DB=/data/recipes.db

export GUNICORN_MEDIA=1

export ALLOWED_HOSTS=*

export DEBUG=0

export SORT_TREE_BY_NAME=0
export ENABLE_PDF_EXPORT=1

bashio::log.info "Starting Tandoor..."
cd /opt/recipes || exit
./boot.sh