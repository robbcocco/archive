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

# prefer Canon's own driver (cnijfilter2) when installed: vendor color
# rendering; fall back to driverless. NB: preset keywords are written for
# the Canon PPD and are ignored with a warning under the fallback
CANON_MODEL=$(lpinfo -m 2>/dev/null | grep -i "canon" | grep -i "${CANON_MODEL_MATCH:-G600}" | grep -viE "driverless|everywhere|gutenprint" | head -1 | cut -d" " -f1)

if [ -n "$CANON_MODEL" ]; then
    echo "Using Canon driver: $CANON_MODEL"
else
    echo "Canon driver not found, using driverless queues"
fi

create_queue () {
    NAME=$1

    # remove first (also unregisters colord profiles), then recreate:
    # self-heals queues from previous boots
    echo "Creating queue: $NAME"

    lpadmin -x "$NAME" 2>/dev/null || true

    if [ -n "$CANON_MODEL" ]; then
        lpadmin \
            -p "$NAME" \
            -E \
            -v "${CANON_PRINTER_URI:-$PRINTER_URI}" \
            -m "$CANON_MODEL"
    else
        lpadmin \
            -p "$NAME" \
            -E \
            -v "$PRINTER_URI" \
            -m everywhere
    fi
}

apply_preset () {
    NAME=$1
    FILE="/presets/${NAME}.conf"
    WANT_COLOR=""

    if [ -f "$FILE" ]; then
        echo "Applying preset $NAME"

        # `|| [ -n "$key" ]` keeps a last line without trailing newline
        while IFS='=' read -r key value || [ -n "$key" ]
        do
            [ -z "$key" ] && continue
            [ "${key:0:1}" = "#" ] && continue
            [ "$key" = "print-color-mode" ] && WANT_COLOR="$value"

            # server-side queue default: applies to jobs from any client;
            # lpoptions would only cover lp runs inside this container.
            # non-fatal: keywords may not exist under the fallback driver
            lpadmin -p "$NAME" -o "$key-default=$value" \
                || echo "warn: $NAME rejected $key=$value"
        done < "$FILE"
    fi

    # PPD queues keep printing from their own ColorModel default (the Canon
    # PPD ships grayscale) regardless of print-color-mode; align it. Choice
    # names vary per driver, so pick from what the queue actually offers
    if [ -n "$WANT_COLOR" ]; then
        case "$WANT_COLOR" in
            monochrome) PATTERN='gray|grey|mono' ;;
            *)          PATTERN='rgb|color' ;;
        esac
        CM=$(lpoptions -p "$NAME" -l 2>/dev/null \
            | sed -n 's/^ColorModel[^:]*: //p' | tr ' ' '\n' | sed 's/^\*//' \
            | grep -iE -m1 "$PATTERN" || true)
        if [ -n "$CM" ]; then
            echo "Aligning ColorModel=$CM on $NAME"
            lpadmin -p "$NAME" -o "ColorModel-default=$CM" \
                || echo "warn: $NAME rejected ColorModel=$CM"
        fi
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