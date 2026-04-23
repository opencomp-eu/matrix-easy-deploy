FROM docker:27-cli

RUN apk add --no-cache bash curl openssl python3 git

WORKDIR /usr/local/share/matrix-easy-deploy

COPY . .
RUN chmod +x /usr/local/share/matrix-easy-deploy/scripts/container-entrypoint.sh \
    && chmod +x /usr/local/share/matrix-easy-deploy/matrix-wizard.sh \
    && chmod +x /usr/local/share/matrix-easy-deploy/start.sh \
    && chmod +x /usr/local/share/matrix-easy-deploy/stop.sh \
    && chmod +x /usr/local/share/matrix-easy-deploy/update.sh

ENTRYPOINT ["/usr/local/share/matrix-easy-deploy/scripts/container-entrypoint.sh"]
CMD ["bash", "matrix-wizard.sh"]
