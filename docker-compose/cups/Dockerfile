FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update && \
    apt install -y \
        cups \
        cups-client \
        cups-filters \
        cups-ipp-utils \
        avahi-daemon \
        dbus \
        printer-driver-gutenprint \
        iputils-ping \
    && apt clean && rm -rf /var/lib/apt/lists/*

# optional: Canon cnijfilter2 driver (drop the .deb in driver/, see README);
# gives a photo-canon queue with Canon's own color rendering
COPY driver/ /tmp/driver/
RUN if ls /tmp/driver/*.deb >/dev/null 2>&1; then \
        apt update && \
        apt install -y /tmp/driver/*.deb && \
        apt clean && rm -rf /var/lib/apt/lists/*; \
    fi && rm -rf /tmp/driver

# root must be in SystemGroup or lpadmin from the entrypoint gets Unauthorized
RUN echo "SystemGroup lpadmin root" >> /etc/cups/cups-files.conf

COPY cupsd.conf /etc/cups/cupsd.conf
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 631

ENTRYPOINT ["/entrypoint.sh"]