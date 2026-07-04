#!/bin/bash
set -e

echo "==> Starting CUPS..."
cupsd

echo "==> Waiting for CUPS..."
until lpstat -r >/dev/null 2>&1
do
    sleep 1
done

HOST=$(echo "$PRINTER_URI" | sed -E 's#ipp[s]?://([^/]+)/.*#\1#')

echo "==> Waiting for printer $HOST..."

until ping -c1 -W1 "$HOST" >/dev/null 2>&1
do
    sleep 5
done

echo "Printer online."

create_queue () {
    NAME=$1

    if ! lpstat -p "$NAME" >/dev/null 2>&1
    then
        echo "Creating queue: $NAME"

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

    if [ -f "$FILE" ]; then
        echo "Applying preset $NAME"

        while IFS='=' read -r key value
        do
            [ -z "$key" ] && continue
            [ "${key:0:1}" = "#" ] && continue

            lpoptions -p "$NAME" -o "$key=$value"
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

# default queue
lpoptions -d photo

echo "==> Ready"

tail -F /var/log/cups/error_log