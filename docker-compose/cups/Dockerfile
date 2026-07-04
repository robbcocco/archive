FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update && \
    apt install -y \
        cups \
        cups-client \
        cups-filters \
        avahi-daemon \
        dbus \
        printer-driver-gutenprint \
        iputils-ping \
    && apt clean && rm -rf /var/lib/apt/lists/*

# root must be in SystemGroup or lpadmin from the entrypoint gets Unauthorized
RUN echo "SystemGroup lpadmin root" >> /etc/cups/cups-files.conf

COPY cupsd.conf /etc/cups/cupsd.conf
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 631

ENTRYPOINT ["/entrypoint.sh"]