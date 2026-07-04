#!/bin/bash
set -e

# web admin login; skipped when no password is provided
if [ -n "$CUPS_ADMIN_PASSWORD" ]; then
    ADMIN_USER="${CUPS_ADMIN_USER:-admin}"
    echo "==> Provisioning admin user $ADMIN_USER..."

    if ! id "$ADMIN_USER" >/dev/null 2>&1; then
        useradd -r -G lpadmin -s /usr/sbin/nologin "$ADMIN_USER"
    fi
    echo "$ADMIN_USER:$CUPS_ADMIN_PASSWORD" | chpasswd
fi

echo "==> Starting dbus/avahi (dnssd advertising)..."
# /run survives container restarts; stale pid files block daemon startup
rm -f /run/dbus/pid /run/avahi-daemon/pid
mkdir -p /run/dbus
dbus-daemon --system --fork
avahi-daemon -D

echo "==> Starting CUPS..."
cupsd

echo "==> Waiting for CUPS..."
# lpstat -r exits 0 even when the scheduler is down; check the message
until lpstat -r 2>/dev/null | grep -q "is running"
do
    sleep 1
done

HOST=$(echo "$PRINTER_URI" | sed -E 's#ipp[s]?://([^/]+)/.*#\1#')

echo "==> Waiting for printer $HOST..."

until ping -c1 -W1 "$HOST" >/dev/null 2>&1
do
    sleep 5
done

# ping only proves the host is up; the everywhere probe needs IPP answering,
# otherwise lpadmin silently falls back to a raw queue
echo "==> Waiting for IPP on $HOST..."
until ipptool -T 5 "$PRINTER_URI" get-printer-attributes.test >/dev/null 2>&1
do
    sleep 5
done

echo "Printer online."

# presets are the single source of truth: without this, options removed
# from a preset file would survive restarts via the stored defaults
rm -f /etc/cups/lpoptions

create_queue () {
    NAME=$1

    # remove first (also unregisters colord profiles), then recreate:
    # self-heals queues that ended up raw on a previous boot
    echo "Creating queue: $NAME"

    lpadmin -x "$NAME" 2>/dev/null || true
    lpadmin \
        -p "$NAME" \
        -E \
        -v "$PRINTER_URI" \
        -m everywhere
}

apply_preset () {
    NAME=$1
    FILE="/presets/${NAME}.conf"

    if [ -f "$FILE" ]; then
        echo "Applying preset $NAME"

        # `|| [ -n "$key" ]` keeps a last line without trailing newline
        while IFS='=' read -r key value || [ -n "$key" ]
        do
            [ -z "$key" ] && continue
            [ "${key:0:1}" = "#" ] && continue

            # server-side queue default: applies to jobs from any client;
            # lpoptions would only cover lp runs inside this container
            lpadmin -p "$NAME" -o "$key-default=$value"
        done < "$FILE"
    fi
}

for preset_file in /presets/*.conf
do
    [ -e "$preset_file" ] || continue

    NAME=$(basename "$preset_file" .conf)

    create_queue "$NAME"
    apply_preset "$NAME"
done

# default queue (lpoptions prints the full option list; not useful in logs)
lpoptions -d "${DEFAULT_PRESET:-photo}" >/dev/null

echo "==> Ready"

touch /var/log/cups/error_log
tail -F /var/log/cups/error_log