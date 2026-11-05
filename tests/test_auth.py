"""Tests for authentication.

This is the code standing between a public URL and your positions, so the
tests lean on the negative cases: wrong passwords, tampered tokens, expired
sessions, and the brute-force path.
"""

from __future__ import annotations

import time

import pytest

from screener.auth import (
    DEFAULT_SESSION_TTL,
    AuthConfig,
    LoginThrottle,
    hash_password,
    make_token,
    verify_password,
    verify_token,
)

PASSWORD = "correct horse battery staple"


class TestPasswordHashing:
    def test_round_trip(self):
        assert verify_password(PASSWORD, hash_password(PASSWORD)) is True

    def test_wrong_password_fails(self):
        assert verify_password("wrong", hash_password(PASSWORD)) is False

    def test_near_miss_fails(self):
        stored = hash_password(PASSWORD)
        assert verify_password(PASSWORD + " ", stored) is False
        assert verify_password(PASSWORD.upper(), stored) is False

    def test_hash_is_salted(self):
        """Two hashes of the same password must differ."""
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_same_salt_is_deterministic(self):
        salt = b"0123456789abcdef"
        assert hash_password(PASSWORD, salt) == hash_password(PASSWORD, salt)

    def test_hash_does_not_contain_the_password(self):
        assert PASSWORD not in hash_password(PASSWORD)

    def test_recognisable_format(self):
        scheme, salt, digest = hash_password(PASSWORD).split("$")
        assert scheme == "scrypt"
        assert len(bytes.fromhex(salt)) == 16
        assert len(bytes.fromhex(digest)) == 32

    def test_empty_password_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            hash_password("")

    @pytest.mark.parametrize("stored", [
        "", "garbage", "scrypt$onlytwo", "bcrypt$aa$bb", "scrypt$zz$zz",
    ])
    def test_malformed_hash_fails_closed(self, stored):
        assert verify_password(PASSWORD, stored) is False

    def test_empty_candidate_fails(self):
        assert verify_password("", hash_password(PASSWORD)) is False


class TestSessionTokens:
    SECRET = "a" * 64

    def test_round_trip(self):
        assert verify_token(make_token(self.SECRET), self.SECRET) is True

    def test_tampered_payload_fails(self):
        token = make_token(self.SECRET)
        payload, signature = token.split(".")
        assert verify_token(f"{payload}x.{signature}", self.SECRET) is False

    def test_tampered_signature_fails(self):
        token = make_token(self.SECRET)
        assert verify_token(token[:-2] + "xy", self.SECRET) is False

    def test_wrong_secret_fails(self):
        assert verify_token(make_token(self.SECRET), "b" * 64) is False

    def test_expired_token_fails(self):
        token = make_token(self.SECRET, ttl_seconds=10, now=time.time() - 100)
        assert verify_token(token, self.SECRET) is False

    def test_token_valid_until_it_expires(self):
        now = time.time()
        token = make_token(self.SECRET, ttl_seconds=60, now=now)
        assert verify_token(token, self.SECRET, now=now + 30) is True
        assert verify_token(token, self.SECRET, now=now + 90) is False

    @pytest.mark.parametrize("token", ["", "nodot", "a.b.c", "!!!.???", "."])
    def test_garbage_tokens_fail_closed(self, token):
        assert verify_token(token, self.SECRET) is False

    def test_empty_secret_fails(self):
        assert verify_token(make_token(self.SECRET), "") is False


class TestAuthConfig:
    def test_disabled_without_a_password(self):
        config = AuthConfig.from_env({})
        assert config.enabled is False

    def test_enabled_by_a_hash(self):
        config = AuthConfig.from_env({"SCREENER_PASSWORD_HASH": hash_password(PASSWORD)})
        assert config.enabled is True
        assert config.check(PASSWORD) is True
        assert config.check("wrong") is False

    def test_plaintext_password_is_hashed_on_load(self):
        config = AuthConfig.from_env({"SCREENER_PASSWORD": PASSWORD})
        assert config.enabled is True
        assert config.check(PASSWORD) is True
        assert PASSWORD not in (config.password_hash or "")

    def test_hash_wins_over_plaintext(self):
        config = AuthConfig.from_env({
            "SCREENER_PASSWORD_HASH": hash_password(PASSWORD),
            "SCREENER_PASSWORD": "something else",
        })
        assert config.check(PASSWORD) is True

    def test_secret_is_derived_from_the_hash(self):
        stored = hash_password(PASSWORD)
        a = AuthConfig.from_env({"SCREENER_PASSWORD_HASH": stored})
        b = AuthConfig.from_env({"SCREENER_PASSWORD_HASH": stored})
        assert a.secret_key == b.secret_key
        assert a.secret_key

    def test_changing_the_password_invalidates_sessions(self):
        """A password change must sign out everyone, including you."""
        old = AuthConfig.from_env({"SCREENER_PASSWORD_HASH": hash_password(PASSWORD)})
        token = old.issue()
        new = AuthConfig.from_env({"SCREENER_PASSWORD_HASH": hash_password("a new password")})
        assert old.validate(token) is True
        assert new.validate(token) is False

    def test_explicit_secret_is_honoured(self):
        config = AuthConfig.from_env({
            "SCREENER_PASSWORD_HASH": hash_password(PASSWORD),
            "SCREENER_SECRET_KEY": "x" * 64,
        })
        assert config.secret_key == "x" * 64

    def test_session_length_is_configurable(self):
        config = AuthConfig.from_env({
            "SCREENER_PASSWORD_HASH": hash_password(PASSWORD),
            "SCREENER_SESSION_DAYS": "1",
        })
        assert config.session_ttl == 24 * 3600

    def test_bad_session_length_falls_back(self):
        config = AuthConfig.from_env({
            "SCREENER_PASSWORD_HASH": hash_password(PASSWORD),
            "SCREENER_SESSION_DAYS": "not a number",
        })
        assert config.session_ttl == DEFAULT_SESSION_TTL

    def test_cookies_are_secure_by_default(self):
        assert AuthConfig.from_env({}).secure_cookie is True

    def test_insecure_cookie_requires_opting_in(self):
        config = AuthConfig.from_env({"SCREENER_INSECURE_COOKIE": "1"})
        assert config.secure_cookie is False

    def test_disabled_config_rejects_every_password(self):
        config = AuthConfig.from_env({})
        assert config.check("") is False
        assert config.check(PASSWORD) is False


class TestLoginThrottle:
    def test_allows_attempts_under_the_limit(self):
        throttle = LoginThrottle(max_attempts=3)
        for _ in range(2):
            throttle.record_failure("1.2.3.4")
        assert throttle.blocked("1.2.3.4") is False

    def test_blocks_at_the_limit(self):
        throttle = LoginThrottle(max_attempts=3)
        for _ in range(3):
            throttle.record_failure("1.2.3.4")
        assert throttle.blocked("1.2.3.4") is True

    def test_clients_are_tracked_separately(self):
        throttle = LoginThrottle(max_attempts=2)
        throttle.record_failure("1.2.3.4")
        throttle.record_failure("1.2.3.4")
        assert throttle.blocked("1.2.3.4") is True
        assert throttle.blocked("5.6.7.8") is False

    def test_success_clears_the_record(self):
        throttle = LoginThrottle(max_attempts=2)
        throttle.record_failure("1.2.3.4")
        throttle.record_success("1.2.3.4")
        throttle.record_failure("1.2.3.4")
        assert throttle.blocked("1.2.3.4") is False

    def test_window_expires(self):
        throttle = LoginThrottle(max_attempts=2, window_seconds=60)
        now = time.time()
        throttle.record_failure("1.2.3.4", now=now - 120)
        throttle.record_failure("1.2.3.4", now=now - 120)
        assert throttle.blocked("1.2.3.4", now=now) is False

    def test_reports_when_to_retry(self):
        throttle = LoginThrottle(max_attempts=1, window_seconds=600)
        now = time.time()
        throttle.record_failure("1.2.3.4", now=now)
        assert 0 < throttle.retry_after("1.2.3.4", now=now) <= 600

    def test_retry_after_is_zero_when_clean(self):
        assert LoginThrottle().retry_after("1.2.3.4") == 0
