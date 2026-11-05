# Container image for a hosted deployment.
#
# The dashboard was built local-only. Running it on the internet needs three
# things this image provides: a password (enforced at startup — the app refuses
# a public bind without one), a writable volume for the database, and a port
# assigned by the host.
#
#   docker build -t screener .
#   docker run -p 8765:8765 \
#     -e SCREENER_PASSWORD_HASH='scrypt$...' \
#     -e SCREENER_INSECURE_COOKIE=1 \
#     -v screener-data:/data screener
#
# Drop SCREENER_INSECURE_COOKIE in production — session cookies should be
# Secure, which requires HTTPS. Hosting platforms terminate TLS for you.

FROM python:3.12-slim

# Fail fast, log immediately, and keep the image free of .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SCREENER_DB=/data/screener.sqlite

WORKDIR /app

# Dependencies first so a code change does not re-run the install layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY screener/ ./screener/
COPY scripts/ ./scripts/
COPY pyproject.toml README.md ./

# Unprivileged user, and a volume it can actually write to. SQLite needs write
# access to the *directory* as well as the file, for its journal.
RUN useradd --create-home --uid 10001 screener \
    && mkdir -p /data \
    && chown -R screener:screener /data /app
USER screener

EXPOSE 8765

# The host's own probe is the real check; this helps `docker run` locally.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen( \
f'http://127.0.0.1:{os.environ.get(\"PORT\", 8765)}/health', timeout=4).status == 200 else 1)"

# --host 0.0.0.0 is required inside a container. The app refuses to start that
# way unless SCREENER_PASSWORD_HASH is set, so a misconfigured deploy fails
# loudly rather than coming up unprotected.
CMD ["python", "-m", "screener", "serve", "--host", "0.0.0.0"]
