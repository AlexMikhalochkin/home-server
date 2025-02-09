ARG version=latest

FROM eclipse-mosquitto:$version
ENV BRIDGE_ADDRESS=""

RUN apk add --no-cache tzdata && \
    rm -rf /var/cache/apk/*

COPY mosquitto.conf /mosquitto/config/mosquitto.conf
COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

ENTRYPOINT ["usr/local/bin/start.sh"]

CMD ["/usr/sbin/mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
