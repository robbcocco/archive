FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update && \
    apt install -y \
        cups \
        cups-client \
        cups-filters \
        avahi-daemon \
        printer-driver-gutenprint \
        iputils-ping \
    && apt clean

COPY cupsd.conf /etc/cups/cupsd.conf
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 631

ENTRYPOINT ["/entrypoint.sh"]