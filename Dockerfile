# Remedy Mesh — single-container deploy for Cloud Run.
# Serves the SPA, the API, and the Twilio voice webhook from one origin.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Python deps first (cached layer)
COPY remedy_mesh_app/requirements.txt /app/remedy_mesh_app/requirements.txt
RUN pip install -r /app/remedy_mesh_app/requirements.txt

# App code + data corpus + frontend, preserving the repo layout so data.py's
# parents[2] data-dir and the frontend index both resolve inside the container.
COPY remedy_mesh_app/remedy_mesh /app/remedy_mesh_app/remedy_mesh
COPY remedy_mesh_synthetic_data/input /app/remedy_mesh_synthetic_data/input
COPY frontend/index.html /app/frontend/index.html

WORKDIR /app/remedy_mesh_app
# Cloud Run injects $PORT; bind to it. --proxy-headers + forwarded-allow-ips so the
# app sees the real https:// scheme/host behind Cloud Run's proxy (correct Twilio webhook URL).
CMD ["sh", "-c", "exec uvicorn remedy_mesh.server:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips=*"]
