

ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add cups cups-filters avahi

COPY rootfs /

WORKDIR /app

RUN chmod a+x run.sh

CMD [ "/app/run.sh" ]