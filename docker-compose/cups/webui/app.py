import hmac
import json
import os
import re
import subprocess
import tempfile
import time

import io

from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageCms, ImageEnhance, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

CUPS = os.environ.get("CUPS_SERVER", "localhost:631")
PORT = int(os.environ.get("WEBUI_PORT", "8631"))
PRINTER_URI = os.environ.get("PRINTER_URI", "")
WEBUI_USER = os.environ.get("WEBUI_USER", "print")
WEBUI_PASSWORD = os.environ.get("WEBUI_PASSWORD", "")
HISTORY_FILE = "/data/history.jsonl"


# options the form may pass through to lp -o
ALLOWED_OPTIONS = (
    "media",
    "MediaType",
    "sides",
    "print-quality",
    "print-color-mode",
    "fit-to-page",
    "orientation-requested",
    "page-ranges",
)
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")
# page-ranges needs commas: "1-3,5,7-9"
OPTION_PATTERNS = {"page-ranges": re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")}
JOB_ID = re.compile(r"^[A-Za-z0-9_-]+-\d+$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024


@app.before_request
def basic_auth():
    if not WEBUI_PASSWORD or request.path == "/healthz":
        return None
    auth = request.authorization
    if (
        auth
        and auth.type == "basic"
        and hmac.compare_digest(auth.username or "", WEBUI_USER)
        and hmac.compare_digest(auth.password or "", WEBUI_PASSWORD)
    ):
        return None
    return "", 401, {"WWW-Authenticate": 'Basic realm="print"'}


@app.get("/healthz")
def healthz():
    return "ok"


def run(cmd, timeout=10):
    """subprocess.run that degrades to rc=1 instead of raising."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def queues():
    out = run(["lpstat", "-h", CUPS, "-e"])
    if out.returncode != 0:
        return []
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

    out = run(["lpoptions", "-h", CUPS, "-p", queue])
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
            elif key == "ColorModel":
                # PPD queues report color the same way; explicit
                # print-color-mode (direct assignment above) still wins
                defaults.setdefault(
                    "print-color-mode",
                    "monochrome"
                    if re.search(r"gray|grey|mono|^k", value, re.I)
                    else "color",
                )

    groups = queue_options(queue)
    media_types = next(
        (g["choices"] for g in groups if g["key"] == "MediaType"), []
    )

    return jsonify(
        defaults=defaults,
        media_types=media_types,
        options=groups,
        icc=printer_icc() is not None,
    )


def queue_options(queue):
    """Every option group the queue's PPD/IPP exposes: key, human label,
    choices, default (the starred choice)."""
    out = run(["lpoptions", "-h", CUPS, "-p", queue, "-l"])
    groups = []
    for line in out.stdout.splitlines():
        head, _, values = line.partition(":")
        if not values.strip():
            continue
        key, _, label = head.partition("/")
        choices, default = [], ""
        for v in values.split():
            if v.startswith("*"):
                v = v[1:]
                default = v
            choices.append(v)
        groups.append(
            {"key": key, "label": label or key, "choices": choices, "default": default}
        )
    return groups


@app.get("/queue/<queue>/raw")
def queue_raw(queue):
    """Raw lpoptions output, for debugging what a queue really defaults to."""
    if queue not in queues():
        return jsonify(error="unknown queue"), 404
    stored = run(["lpoptions", "-h", CUPS, "-p", queue])
    groups = run(["lpoptions", "-h", CUPS, "-p", queue, "-l"])
    body = "# lpoptions -p " + queue + "\n" + stored.stdout.replace(" ", "\n")
    body += "\n# lpoptions -p " + queue + " -l\n" + groups.stdout
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/jobs")
def jobs():
    out = run(["lpstat", "-h", CUPS, "-o"])
    result = []
    for line in out.stdout.splitlines():
        # "photo-42  guest  230400  Mon 06 Jul 2026 12:00:00 PM UTC"
        parts = line.split(None, 3)
        if not parts or not JOB_ID.match(parts[0]):
            continue
        queue, _, num = parts[0].rpartition("-")
        result.append(
            {
                "id": parts[0],
                "queue": queue,
                "number": num,
                "size": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                "when": parts[3] if len(parts) > 3 else "",
            }
        )
    return jsonify(result)


@app.post("/cancel/<job_id>")
def cancel(job_id):
    if not JOB_ID.match(job_id):
        return jsonify(error="bad job id"), 400
    out = run(["cancel", "-h", CUPS, job_id])
    if out.returncode != 0:
        return jsonify(error=out.stderr.strip() or "cancel failed"), 502
    return jsonify(ok=True)


_ink_cache = {"at": 0.0, "data": None}


def _ipp_attr(text, name):
    m = re.search(rf"^\s*{name} \([^)]*\) = (.*)$", text, re.M)
    return [v.strip() for v in m.group(1).split(",")] if m else []


@app.get("/ink")
def ink():
    """marker-levels straight from the printer; cached, polling cupsd's
    upstream on every page load would wake the printer constantly."""
    if not PRINTER_URI:
        return jsonify([])
    if time.time() - _ink_cache["at"] > 300 or _ink_cache["data"] is None:
        out = run(
            ["ipptool", "-T", "10", "-tv", PRINTER_URI, "get-printer-attributes.test"],
            timeout=15,
        )
        names = _ipp_attr(out.stdout, "marker-names")
        levels = _ipp_attr(out.stdout, "marker-levels")
        colors = _ipp_attr(out.stdout, "marker-colors")
        data = []
        for i, name in enumerate(names):
            try:
                level = int(levels[i])
            except (IndexError, ValueError):
                level = -1
            if level < 0:  # -1 = unknown per RFC 3805
                continue
            data.append(
                {
                    "name": name,
                    "level": level,
                    "color": colors[i] if i < len(colors) else "",
                }
            )
        _ink_cache.update(at=time.time(), data=data)
    return jsonify(_ink_cache["data"])


def log_history(entry):
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # no /data volume mounted: history simply off


@app.get("/history")
def history():
    try:
        with open(HISTORY_FILE) as f:
            lines = f.readlines()[-30:]
    except OSError:
        return jsonify([])
    entries = []
    for line in reversed(lines):
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return jsonify(entries)


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


def proof_transform(icc):
    srgb = ImageCms.createProfile("sRGB")
    return ImageCms.buildProofTransform(
        srgb, srgb, icc, "RGB", "RGB",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        proofRenderingIntent=ImageCms.Intent.PERCEPTUAL,
        flags=ImageCms.Flags.SOFTPROOFING,
    )


def apply_profile(path, profile):
    with Image.open(path) as img:
        if profile == "icc":
            # gamut-map: sRGB -> printer space (perceptual) -> sRGB.
            # out-of-gamut colors get compressed the way the vendor driver
            # would, before the driverless path's own (clipping) transform
            img = ImageOps.exif_transpose(img).convert("RGB")
            img = ImageCms.applyTransform(img, proof_transform(printer_icc()))
        else:
            img = apply_ops(img, PROFILES[profile])
        out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(out.name, "JPEG", quality=95)
    os.unlink(path)
    return out.name


def is_pdf(upload):
    return upload.mimetype == "application/pdf" or upload.filename.lower().endswith(
        ".pdf"
    )


def pdf_page(upload, page=1):
    """Rasterize one page so PDFs go through the same preview pipeline
    (and get soft-proofed) instead of a raw <embed>."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        upload.stream.seek(0)
        upload.save(tmp.name)
        prefix = tmp.name + "-pg"
        out = run(
            ["pdftoppm", "-png", "-r", "120", "-f", str(page), "-l", str(page),
             "-singlefile", tmp.name, prefix],
            timeout=30,
        )
        if out.returncode != 0:
            raise ValueError(out.stderr.strip() or "pdftoppm failed")
        img = Image.open(prefix + ".png")
        img.load()
        os.unlink(prefix + ".png")
    return img


@app.post("/preview")
def preview():
    """Render the thumbnail through the real pillow pipeline; when a printer
    ICC profile is mounted, additionally soft-proof how the print will look."""
    upload = request.files.get("file")
    profile = request.form.get("profile", "")
    color_mode = request.form.get("print-color-mode", "")
    if upload is None or upload.filename == "":
        return jsonify(error="no file"), 400
    if profile and profile != "icc" and profile not in PROFILES:
        return jsonify(error=f"unknown profile: {profile}"), 400

    try:
        if is_pdf(upload):
            # preview the first page that will actually print
            page = 1
            m = re.match(r"^(\d+)", request.form.get("page-ranges", ""))
            if m:
                page = max(1, int(m.group(1)))
            try:
                img = pdf_page(upload, page)
            except ValueError:
                if page == 1:
                    raise
                img = pdf_page(upload)  # range past the last page
            # rendered at 120dpi, so px/120 = the true page size
            dpi = 120
            # profiles are image-only at print time; page previews unmodified
            img = apply_ops(img, {})
        else:
            img = Image.open(upload.stream)
            # cups imagetopdf assumes 128ppi when the file reports none
            dpi = img.info.get("dpi", (0,))[0] or 128
            # "icc" has no pillow ops; its preview is the soft-proof itself
            img = apply_ops(img, PROFILES.get(profile, {}))
        if color_mode == "monochrome":
            # driver grays out after profile ops; mirror that order
            img = ImageOps.grayscale(img).convert("RGB")
        px_w, px_h = img.size
        img.thumbnail((640, 640))
    except Exception as exc:
        return jsonify(error=f"preview failed: {exc}"), 400

    proofed = False
    icc = printer_icc()
    if icc:
        try:
            img = ImageCms.applyTransform(img, proof_transform(icc))
            proofed = True
        except Exception:
            pass  # bad/unsupported profile: fall back to unproofed preview

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    buf.seek(0)
    response = send_file(buf, mimetype="image/jpeg")
    response.headers["X-Soft-Proof"] = "1" if proofed else "0"
    # original raster size + density: lets the client draw "as-is"
    # scaling at true physical size on the paper frame
    response.headers["X-Image-Px"] = f"{px_w}x{px_h}"
    response.headers["X-Image-DPI"] = str(round(dpi))
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

    options = {}
    cmd = ["lp", "-h", CUPS, "-d", queue, "-n", copies, "-t", upload.filename]
    for key in ALLOWED_OPTIONS:
        value = request.form.get(key, "")
        if value:
            if not OPTION_PATTERNS.get(key, SAFE_VALUE).match(value):
                return jsonify(error=f"bad value for {key}"), 400
            options[key] = value
            cmd += ["-o", f"{key}={value}"]

    # PPD-specific options (CNIJ*, Resolution, ...): accept anything the
    # queue itself advertises, value must be one of its listed choices
    handled = set(ALLOWED_OPTIONS) | {"file", "queue", "copies", "profile"}
    advertised = {g["key"]: set(g["choices"]) for g in queue_options(queue)}
    for key, value in request.form.items():
        if key in handled or not value:
            continue
        choices = advertised.get(key)
        if choices is None:
            continue  # not a printer option: ignore
        if value not in choices or not SAFE_VALUE.match(value):
            return jsonify(error=f"bad value for {key}"), 400
        options[key] = value
        cmd += ["-o", f"{key}={value}"]

    suffix = os.path.splitext(upload.filename)[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        upload.save(tmp.name)
        path = tmp.name

    profile = request.form.get("profile", "")
    if profile:
        if is_pdf(upload):
            os.unlink(path)
            return jsonify(error="color profiles are for images only"), 400
        if profile == "icc" and printer_icc() is None:
            os.unlink(path)
            return jsonify(error="no printer icc profile mounted"), 400
        if profile != "icc" and profile not in PROFILES:
            os.unlink(path)
            return jsonify(error=f"unknown profile: {profile}"), 400
        try:
            path = apply_profile(path, profile)
        except Exception as exc:
            os.unlink(path)
            return jsonify(error=f"profile failed: {exc}"), 400
    try:
        result = run(cmd + [path], timeout=60)
    finally:
        os.unlink(path)

    if result.returncode != 0:
        return jsonify(error=result.stderr.strip() or "lp failed"), 502

    # "request id is photo-42 (1 file(s))"
    message = result.stdout.strip()
    m = re.search(r"request id is (\S+)", message)
    job_id = m.group(1) if m else ""
    log_history(
        {
            "ts": int(time.time()),
            "file": upload.filename,
            "queue": queue,
            "copies": int(copies),
            "options": options,
            "profile": profile,
            "job": job_id,
        }
    )
    return jsonify(ok=True, message=message, job=job_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
