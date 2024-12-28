ARG BUILD_FROM
FROM $BUILD_FROM

RUN apt update && apt install -y --no-install-recommends cups cups-filters avahi-daemon dbus-daemon && apt clean && rm -rf /var/lib/apt/lists/*

COPY rootfs /
WORKDIR /app

RUN tar -xvf linux-UFRII-drv-v600-m17n.tar
RUN cd linux-UFRII-drv-v600-m17n && \
    echo y | ./install.sh

EXPOSE 631
CMD [ "/app/run.sh" ]