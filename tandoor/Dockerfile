ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache postgresql-libs postgresql-client \
    gettext zlib libjpeg libwebp libxml2-dev libxslt-dev openldap git 

ARG VERSION=1.5.24
RUN wget https://github.com/TandoorRecipes/recipes/archive/refs/tags/$VERSION.tar.gz && \
    tar -xvzf $VERSION.tar.gz && \
    rm $VERSION.tar.gz && \
    mv recipes-$VERSION /opt/recipes

WORKDIR /opt/recipes
RUN mkdir mediafiles && chmod 755 mediafiles && \
    mkdir staticfiles && chmod 755 staticfiles

RUN if [ `apk --print-arch` = "armv7" ]; \
    then printf "[global]\nextra-index-url=https://www.piwheels.org/simple\n" > /etc/pip.conf ; \
    fi 

RUN sed -i '/# Development/,$d' requirements.txt 
RUN apk add --no-cache --virtual .build-deps gcc musl-dev \
    postgresql-dev zlib-dev jpeg-dev libwebp-dev openssl-dev \
    libffi-dev cargo openldap-dev python3-dev yarn && \
    echo -n "INPUT ( libldap.so )" > /usr/lib/libldap_r.so && \
    python -m venv venv && \
    /opt/recipes/venv/bin/python -m pip install --upgrade pip && \
    venv/bin/pip install wheel==0.42.0 && \
    venv/bin/pip install setuptools_rust==1.9.0 && \
    venv/bin/pip install -r requirements.txt --no-cache-dir && \
    yarn --cwd vue install && \
    yarn --cwd vue build && \
    apk --purge del .build-deps 

RUN chmod a+x boot.sh 

ENV PYTHONUNBUFFERED=1
ENV DOCKER=true
EXPOSE 8080

COPY run.sh .
RUN chmod a+x run.sh

CMD [ "/opt/recipes/run.sh" ]