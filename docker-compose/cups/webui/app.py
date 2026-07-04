import os
import re
import subprocess
import tempfile

import io

from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageCms, ImageEnhance, ImageOps

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


# print looks compensating for cups-filters' flat rasterization;
# "vivid" is tuned to approximate the macOS/AirPrint output
PROFILES = {
    "vivid": {"saturation": 1.20, "contrast": 1.12},
    "punch": {"saturation": 1.35, "contrast": 1.20},
    "warm": {"saturation": 1.15, "contrast": 1.10, "warmth": 0.06},
    "soft": {"saturation": 1.05, "contrast": 1.05},
    "bw": {"saturation": 0.0, "contrast": 1.10},
}


ICC_DIR = "/icc"


def printer_icc():
    if os.path.isdir(ICC_DIR):
        for name in sorted(os.listdir(ICC_DIR)):
            if name.lower().endswith((".icc", ".icm")):
                return os.path.join(ICC_DIR, name)
    return None


def apply_ops(img, params):
    img = ImageOps.exif_transpose(img)  # bake rotation before re-saving
    img = img.convert("RGB")
    warmth = params.get("warmth")
    if warmth:
        r, g, b = img.split()
        r = r.point(lambda v: min(255, round(v * (1 + warmth))))
        b = b.point(lambda v: max(0, round(v * (1 - warmth))))
        img = Image.merge("RGB", (r, g, b))
    img = ImageEnhance.Color(img).enhance(params.get("saturation", 1))
    img = ImageEnhance.Contrast(img).enhance(params.get("contrast", 1))
    return img


def apply_profile(path, profile):
    with Image.open(path) as img:
        img = apply_ops(img, PROFILES[profile])
        out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(out.name, "JPEG", quality=95)
    os.unlink(path)
    return out.name


@app.post("/preview")
def preview():
    """Render the thumbnail through the real pillow pipeline; when a printer
    ICC profile is mounted, additionally soft-proof how the print will look."""
    upload = request.files.get("file")
    profile = request.form.get("profile", "")
    if upload is None or upload.filename == "":
        return jsonify(error="no file"), 400
    if profile and profile not in PROFILES:
        return jsonify(error=f"unknown profile: {profile}"), 400

    try:
        img = Image.open(upload.stream)
        img = apply_ops(img, PROFILES[profile] if profile else {})
        img.thumbnail((640, 640))
    except Exception as exc:
        return jsonify(error=f"preview failed: {exc}"), 400

    proofed = False
    icc = printer_icc()
    if icc:
        try:
            srgb = ImageCms.createProfile("sRGB")
            transform = ImageCms.buildProofTransform(
                srgb, srgb, icc, "RGB", "RGB",
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                proofRenderingIntent=ImageCms.Intent.PERCEPTUAL,
                flags=ImageCms.Flags.SOFTPROOFING,
            )
            img = ImageCms.applyTransform(img, transform)
            proofed = True
        except Exception:
            pass  # bad/unsupported profile: fall back to unproofed preview

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    buf.seek(0)
    response = send_file(buf, mimetype="image/jpeg")
    response.headers["X-Soft-Proof"] = "1" if proofed else "0"
    return response


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

    profile = request.form.get("profile", "")
    if profile:
        if profile not in PROFILES:
            os.unlink(path)
            return jsonify(error=f"unknown profile: {profile}"), 400
        try:
            path = apply_profile(path, profile)
        except Exception as exc:
            os.unlink(path)
            return jsonify(error=f"profile failed: {exc}"), 400
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
