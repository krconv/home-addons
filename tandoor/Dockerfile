ARG BUILD_FROM
FROM $BUILD_FROM

#Install all dependencies.
RUN apk add --no-cache postgresql-libs postgresql-client \
    gettext zlib libjpeg libwebp libxml2-dev libxslt-dev \
    openldap git

RUN \
    if [ `apk --print-arch` = "armv7" ]; then \
    printf "[global]\nextra-index-url=https://www.piwheels.org/simple\n" > /etc/pip.conf ; \
    fi

ENV S6_CMD_WAIT_FOR_SERVICES=1 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_SERVICES_GRACETIME=0

ARG VERSION=1.5.24

RUN wget https://github.com/TandoorRecipes/recipes/archive/refs/tags/$VERSION.tar.gz && \
    tar -xvzf $VERSION.tar.gz && \
    rm $VERSION.tar.gz && \
    mv recipes-$VERSION /opt/recipes

WORKDIR /opt/recipes

RUN sed -i '/# Development/,$d' requirements.txt && \
    apk add --no-cache --virtual .build-deps gcc musl-dev postgresql-dev zlib-dev jpeg-dev libwebp-dev openssl-dev libffi-dev cargo openldap-dev python3-dev && \
    echo -n "INPUT ( libldap.so )" > /usr/lib/libldap_r.so && \
    python -m venv venv && \
    venv/bin/python -m pip install --upgrade pip && \
    venv/bin/pip install wheel==0.42.0 && \
    venv/bin/pip install setuptools_rust==1.9.0 && \
    venv/bin/pip install -r requirements.txt --no-cache-dir &&\
    apk --purge del .build-deps

RUN chmod a+x boot.sh

ENV PYTHONUNBUFFERED=1
ENV DOCKER=true
EXPOSE 8080

RUN mkdir -p /data && \
    ln -s /opt/recipes/mediafiles /data/mediafiles

COPY run.sh .
RUN chmod a+x run.sh

CMD [ "/opt/recipes/run.sh" ]