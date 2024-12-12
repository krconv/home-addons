

ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add cups cups-filters 

COPY rootfs/etc/cups/cupsd.conf /etc/cups/cupsd.conf

RUN ln -s /etc/cups /data
    
WORKDIR /app

COPY run.sh .
RUN chmod a+x run.sh

CMD [ "/app/run.sh" ]