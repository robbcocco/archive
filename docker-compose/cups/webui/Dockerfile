FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends cups-client && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask pillow

WORKDIR /app
COPY app.py .
COPY templates templates

CMD ["python", "app.py"]
