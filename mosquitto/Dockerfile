ARG version=latest

FROM eclipse-mosquitto:$version
ENV BRIDGE_ADDRESS=""

RUN apk add --no-cache tzdata && \
    rm -rf /var/cache/apk/*

COPY mosquitto.conf /mosquitto/config/mosquitto.conf

CMD /bin/sh -c 'echo "address $BRIDGE_ADDRESS" >> /mosquitto/config/mosquitto.conf && /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf 2>&1 | grep -v "PING"'
