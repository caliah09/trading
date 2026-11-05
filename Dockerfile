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

# TEMPORARY TEST CREDENTIAL — REMOVE BEFORE THIS IS ANYTHING BUT A TEST.
#
# A baked-in default so a deploy comes up without the platform's environment
# being configured first. It is a real scrypt hash, so the image does not carry
# the password itself, but it is a hash of a password that is now permanently in
# this repository's git history and is therefore shared with anyone who ever has
# read access to the repo.
#
# An environment variable of the same name OVERRIDES this, which is the exit
# path: set SCREENER_PASSWORD_HASH in the platform, confirm the login still
# works, then delete these two lines. Until then, treat the deployed URL as
# protected by a password that is not actually a secret.
ENV SCREENER_PASSWORD_HASH=scrypt$6c97853193ffa1bfba07f989c02bef7d$94c4843d32877ee10fc46ef6cfbeaeb2000c403533a89558605fdb2da5cc25bb

# Unprivileged user for the application process.
#
# The container deliberately STARTS as root so the entrypoint can take
# ownership of a mounted volume, then drops to this user before exec'ing the
# app. Chowning /data here is not enough on its own: a platform that mounts a
# volume at /data replaces this directory with a fresh root-owned one, and the
# app then cannot create its database.
RUN useradd --create-home --uid 10001 screener \
    && mkdir -p /data \
    && chown -R screener:screener /data /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8765

# The host's own probe is the real check; this helps `docker run` locally.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen( \
f'http://127.0.0.1:{os.environ.get(\"PORT\", 8765)}/health', timeout=4).status == 200 else 1)"

# --host 0.0.0.0 is required inside a container. The app refuses to start that
# way unless SCREENER_PASSWORD_HASH is set, so a misconfigured deploy fails
# loudly rather than coming up unprotected.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "screener", "serve", "--host", "0.0.0.0"]
