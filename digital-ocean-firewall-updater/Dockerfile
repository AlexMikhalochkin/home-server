FROM digitalocean/doctl:1.109.0

RUN apk add nmap-ncat

WORKDIR /test
COPY script.sh ./
RUN chmod +x script.sh

ENTRYPOINT ["sh", "script.sh"]
