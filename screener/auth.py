"""Authentication for a hosted deployment.

The dashboard was built local-only and had no authentication, which was correct
while it bound to loopback. Putting it on the internet changes that: without a
login, anyone who finds the URL can read your positions and change your
settings.

Design notes:

* **Passwords are never stored or compared in plaintext.** ``screener
  hash-password`` produces a scrypt hash; that hash is what goes in the host's
  environment. Someone who reads your environment variables still cannot log
  in elsewhere with what they find.
* **Sessions are stateless and signed**, so they survive a restart and need no
  server-side store. The token carries only an expiry and an HMAC over it.
* **The signing key is derived from the password hash** unless one is supplied
  explicitly, so changing your password invalidates every existing session.
  That is the behaviour you want from a password change.
* **Login attempts are throttled per client**, because a single-password login
  on a public URL is otherwise brute-forceable at network speed.

Everything here uses the standard library — no new dependency to audit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

__all__ = [
    "SESSION_COOKIE", "AuthConfig", "LoginThrottle",
    "hash_password", "verify_password", "make_token", "verify_token",
]

SESSION_COOKIE = "screener_session"

#: scrypt parameters. n=2**15 keeps a single verification around 100ms on
#: modest hardware — slow enough to make guessing expensive, fast enough that
#: logging in does not feel broken.
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_LEN = 32

#: scrypt needs 128 * n * r bytes — exactly 32 MB at these parameters, which
#: hits OpenSSL's default cap and raises "memory limit exceeded". The limit has
#: to be raised explicitly; it bounds one hash computation, not the process.
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * _SCRYPT_R * 2

DEFAULT_SESSION_TTL = 7 * 24 * 3600  # a week


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash a password for storage in an environment variable.

    Returns ``scrypt$<salt_hex>$<hash_hex>``.
    """
    if not password:
        raise ValueError("password must not be empty")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_LEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    if not password or not stored:
        return False
    try:
        scheme, salt_hex, digest_hex = stored.split("$", 2)
    except ValueError:
        log.error("Stored password hash is malformed; expected scrypt$salt$hash")
        return False
    if scheme != "scrypt":
        log.error("Unsupported password hash scheme %r", scheme)
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        log.error("Stored password hash contains invalid hex")
        return False

    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
    except ValueError as exc:
        # A hash produced with different parameters cannot be verified here.
        # Failing closed is the only safe answer.
        log.error("Could not verify password hash: %s", exc)
        return False
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------
# Session tokens
# --------------------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def make_token(secret: str, ttl_seconds: int = DEFAULT_SESSION_TTL,
               now: float | None = None) -> str:
    """Issue a signed session token carrying only its own expiry."""
    expiry = int((now if now is not None else time.time()) + ttl_seconds)
    payload = str(expiry).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_token(token: str, secret: str, now: float | None = None) -> bool:
    """Validate a session token's signature and expiry."""
    if not token or not secret:
        return False
    try:
        payload_part, signature_part = token.split(".", 1)
        payload = _unb64(payload_part)
        signature = _unb64(signature_part)
    except (ValueError, Exception):
        return False

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return False

    try:
        expiry = int(payload.decode())
    except (ValueError, UnicodeDecodeError):
        return False
    return expiry > (now if now is not None else time.time())


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class AuthConfig:
    """Resolved authentication settings.

    ``enabled`` is False only when no password is configured at all, which is
    the ordinary local case. The serve command refuses to bind a non-loopback
    address in that state.
    """

    password_hash: str | None = None
    secret_key: str = ""
    session_ttl: int = DEFAULT_SESSION_TTL
    secure_cookie: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.password_hash)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AuthConfig":
        """Build from environment variables.

        ``SCREENER_PASSWORD_HASH``  preferred — output of `screener hash-password`
        ``SCREENER_PASSWORD``       plaintext fallback, hashed on startup
        ``SCREENER_SECRET_KEY``     optional explicit signing key
        ``SCREENER_SESSION_DAYS``   optional session lifetime
        ``SCREENER_INSECURE_COOKIE``set when serving over plain HTTP
        """
        env = env if env is not None else dict(os.environ)

        password_hash = env.get("SCREENER_PASSWORD_HASH") or None
        if not password_hash and env.get("SCREENER_PASSWORD"):
            log.warning(
                "Using SCREENER_PASSWORD (plaintext). Prefer "
                "SCREENER_PASSWORD_HASH from `screener hash-password`, so the "
                "environment never holds a password that works elsewhere."
            )
            password_hash = hash_password(env["SCREENER_PASSWORD"])

        # Deriving the key from the hash means a password change invalidates
        # every outstanding session, which is the expected behaviour.
        secret = env.get("SCREENER_SECRET_KEY") or ""
        if not secret and password_hash:
            secret = hashlib.sha256(
                ("session-key:" + password_hash).encode()
            ).hexdigest()

        try:
            days = float(env.get("SCREENER_SESSION_DAYS", "7"))
        except ValueError:
            days = 7.0

        return cls(
            password_hash=password_hash,
            secret_key=secret,
            session_ttl=int(days * 24 * 3600),
            secure_cookie=not _is_truthy(env.get("SCREENER_INSECURE_COOKIE")),
        )

    def check(self, password: str) -> bool:
        return bool(self.password_hash) and verify_password(password, self.password_hash)

    def issue(self) -> str:
        return make_token(self.secret_key, self.session_ttl)

    def validate(self, token: str) -> bool:
        return verify_token(token, self.secret_key)


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------
# Brute-force protection
# --------------------------------------------------------------------------

@dataclass
class LoginThrottle:
    """Per-client attempt limiting.

    A single-password login on a public URL can otherwise be guessed at network
    speed. Failures are tracked per client address and the window resets on a
    successful login.
    """

    max_attempts: int = 8
    window_seconds: int = 900
    _failures: dict[str, list[float]] = field(default_factory=dict)

    def _recent(self, client: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        attempts = [t for t in self._failures.get(client, []) if t > cutoff]
        self._failures[client] = attempts
        return attempts

    def blocked(self, client: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return len(self._recent(client, now)) >= self.max_attempts

    def retry_after(self, client: str, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        attempts = self._recent(client, now)
        if not attempts:
            return 0
        return max(0, int(attempts[0] + self.window_seconds - now))

    def record_failure(self, client: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._recent(client, now)
        self._failures.setdefault(client, []).append(now)

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)
