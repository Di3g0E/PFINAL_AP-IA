# ============================================================================
# PFINAL_AP-IA — Imagen única para HuggingFace Spaces (Docker SDK)
#
# Particularidades de HF Spaces que condicionan este Dockerfile:
#   - El contenedor corre como UID 1000 (usuario "user"), no como root.
#   - No hay almacenamiento persistente en el plan free: todo el estado va a
#     Supabase. Los modelos (joblib/pt/pkl) viajan dentro de la imagen.
#   - Puerto expuesto convencional: 7860 (configurable vía $PORT).
#   - Los secretos (DATABASE_URL, GROQ_API_KEY, MASTER_FERNET_KEY, etc.) se
#     inyectan desde la UI del Space (Settings → Variables and secrets); no
#     se monta un .env.
#
# Anclajes heredados de P6 (no relajar sin retest):
#   - numpy<2.0          ← paddlepaddle 2.6.2 (P3)
#   - scikit-learn==1.5  ← compat paddleocr + carga de joblibs (P3)
#   - urllib3<2          ← compat paddle/requests
# ============================================================================

FROM python:3.12-slim

# --- Dependencias del sistema (root) ---
# libgl1, libglib2.0-0: opencv-python
# libgomp1: OpenMP (paddle, torch)
# build-essential, gcc: fallback para wheels que requieran compilación
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    build-essential \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Instalar uv como root (vive en /usr/local/bin) ---
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# --- Usuario no-root requerido por HF Spaces ---
RUN useradd -m -u 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:/usr/local/bin:$PATH

WORKDIR /home/user/app

# --- Capa de dependencias (cache friendly) ---
COPY --chown=user:user requirements.txt ./
RUN uv venv .venv --python 3.12 && \
    uv pip install --python .venv/bin/python --link-mode=copy -r requirements.txt

# --- Código de la aplicación ---
COPY --chown=user:user src ./src
COPY --chown=user:user main.py ./
COPY --chown=user:user config ./config
COPY --chown=user:user scripts ./scripts

# Modelos pre-entrenados (P1/P2/P3 + liveness DenseNet). Van en la imagen
# porque HF Spaces free no tiene almacenamiento persistente.
COPY --chown=user:user models ./models

# Directorios escribibles que el runtime espera
RUN mkdir -p ./data ./logs

ENV PATH="/home/user/app/.venv/bin:$PATH" \
    PYTHONPATH="/home/user/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860

EXPOSE 7860

# Respeta $PORT por si HF cambia la convención
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
