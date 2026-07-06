FROM python:3.12-slim

# cups-client: lp/lpstat; cups-ipp-utils: ipptool for ink levels;
# poppler-utils: pdftoppm for PDF previews; curl: compose healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        cups-client \
        cups-ipp-utils \
        poppler-utils \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app
COPY app.py .
COPY templates templates

# shell form so WEBUI_PORT expands; threads because jobs/ink polling is
# I/O-bound subprocess waiting
CMD gunicorn --workers 2 --threads 4 --timeout 120 \
    --bind 0.0.0.0:${WEBUI_PORT:-8631} app:app
