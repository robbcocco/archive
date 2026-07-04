import os
import re
import subprocess
import tempfile

from flask import Flask, jsonify, render_template, request

CUPS = os.environ.get("CUPS_SERVER", "localhost:631")
PORT = int(os.environ.get("WEBUI_PORT", "8631"))

# options the form may pass through to lp -o
ALLOWED_OPTIONS = (
    "media",
    "MediaType",
    "sides",
    "print-quality",
    "print-color-mode",
    "fit-to-page",
    "orientation-requested",
)
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024


def queues():
    out = subprocess.run(
        ["lpstat", "-h", CUPS, "-e"], capture_output=True, text=True, timeout=10
    )
    return sorted(q for q in out.stdout.split() if q)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/queues")
def list_queues():
    return jsonify(queues())


@app.get("/queue/<queue>")
def queue_info(queue):
    if queue not in queues():
        return jsonify(error="unknown queue"), 404

    out = subprocess.run(
        ["lpoptions", "-h", CUPS, "-p", queue],
        capture_output=True, text=True, timeout=10,
    )
    defaults = {}
    quality_names = {"Draft": "3", "Normal": "4", "High": "5"}
    for token in out.stdout.split():
        if "=" in token:
            key, value = token.split("=", 1)
            if key in ALLOWED_OPTIONS:
                defaults[key] = value
            elif key == "cupsPrintQuality" and value in quality_names:
                # PPD queues report quality this way instead of print-quality
                defaults.setdefault("print-quality", quality_names[value])

    out = subprocess.run(
        ["lpoptions", "-h", CUPS, "-p", queue, "-l"],
        capture_output=True, text=True, timeout=10,
    )
    media_types = []
    for line in out.stdout.splitlines():
        head, _, values = line.partition(":")
        if head.split("/")[0] == "MediaType":
            media_types = [v.lstrip("*") for v in values.split()]

    return jsonify(defaults=defaults, media_types=media_types)


@app.post("/print")
def print_job():
    upload = request.files.get("file")
    queue = request.form.get("queue", "")
    copies = request.form.get("copies", "1")

    if upload is None or upload.filename == "":
        return jsonify(error="no file"), 400
    if queue not in queues():
        return jsonify(error=f"unknown queue: {queue}"), 400
    if not copies.isdigit() or not 1 <= int(copies) <= 99:
        return jsonify(error="copies must be 1-99"), 400

    cmd = ["lp", "-h", CUPS, "-d", queue, "-n", copies, "-t", upload.filename]
    for key in ALLOWED_OPTIONS:
        value = request.form.get(key, "")
        if value:
            if not SAFE_VALUE.match(value):
                return jsonify(error=f"bad value for {key}"), 400
            cmd += ["-o", f"{key}={value}"]

    suffix = os.path.splitext(upload.filename)[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        upload.save(tmp.name)
        path = tmp.name
    try:
        result = subprocess.run(
            cmd + [path], capture_output=True, text=True, timeout=60
        )
    finally:
        os.unlink(path)

    if result.returncode != 0:
        return jsonify(error=result.stderr.strip() or "lp failed"), 502
    # "request id is photo-42 (1 file(s))"
    return jsonify(ok=True, message=result.stdout.strip())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
