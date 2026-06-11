"""Security primitives: password hashing, signed tokens, rate limiting.

Stdlib only (hashlib/hmac) — no weak homemade crypto: PBKDF2-HMAC-SHA256 with
per-password random salt, constant-time comparison, HMAC-signed expiring tokens.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import threading
import time
from dataclasses import dataclass

from app.core.exceptions import AuthenticationError


class PasswordHasher:
    """PBKDF2-HMAC-SHA256 with embedded salt + iteration count."""

    def __init__(self, iterations: int = 310_000) -> None:
        self._iterations = iterations

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, self._iterations)
        return f"pbkdf2_sha256${self._iterations}${salt.hex()}${digest.hex()}"

    def verify(self, password: str, stored: str) -> bool:
        try:
            _, iterations, salt_hex, digest_hex = stored.split("$")
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
            )
            return hmac.compare_digest(candidate.hex(), digest_hex)
        except (ValueError, TypeError):
            return False


@dataclass(frozen=True, slots=True)
class TokenPayload:
    subject: str          # user id
    role: str
    expires_at: float
    version: int = 0      # matched against users.token_version -> revocation

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


class TokenManager:
    """Issues and verifies HMAC-SHA256 signed, expiring bearer tokens.

    Tokens embed the user's `token_version`; bumping that column in the DB
    (logout, password change) instantly revokes every outstanding token."""

    def __init__(self, secret_key: str, ttl_seconds: int) -> None:
        self._key = secret_key.encode()
        self._ttl = ttl_seconds

    def issue(self, subject: str, role: str, version: int = 0) -> str:
        payload = {"sub": subject, "role": role, "ver": version,
                   "exp": time.time() + self._ttl}
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        signature = self._sign(body)
        return f"{body.decode()}.{signature}"

    def verify(self, token: str) -> TokenPayload:
        try:
            body_b64, signature = token.split(".", 1)
        except ValueError:
            raise AuthenticationError("Malformed token") from None
        if not hmac.compare_digest(self._sign(body_b64.encode()), signature):
            raise AuthenticationError("Invalid token signature")
        padded = body_b64 + "=" * (-len(body_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        payload = TokenPayload(subject=str(data["sub"]), role=data["role"],
                               expires_at=float(data["exp"]),
                               version=int(data.get("ver", 0)))
        if payload.is_expired:
            raise AuthenticationError("Token expired")
        return payload

    def _sign(self, body: bytes) -> str:
        return hmac.new(self._key, body, hashlib.sha256).hexdigest()


class TotpProvider:
    """RFC 6238 time-based one-time passwords (SHA1, 6 digits, 30s period) —
    compatible with Google Authenticator / Authy / 1Password. Stdlib only."""

    PERIOD_SECONDS = 30
    DIGITS = 6

    def __init__(self, issuer: str = "PyWebFW") -> None:
        self._issuer = issuer

    @staticmethod
    def generate_secret() -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")

    def provisioning_uri(self, secret: str, account: str) -> str:
        """otpauth:// URI for authenticator apps (renderable as a QR code)."""
        return (f"otpauth://totp/{self._issuer}:{account}?secret={secret}"
                f"&issuer={self._issuer}&algorithm=SHA1"
                f"&digits={self.DIGITS}&period={self.PERIOD_SECONDS}")

    def _code_at(self, secret: str, counter: int) -> str:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8))
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        value = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF)
        return f"{value % 10 ** self.DIGITS:0{self.DIGITS}d}"

    def current_code(self, secret: str, at: float | None = None) -> str:
        counter = int((at if at is not None else time.time()) // self.PERIOD_SECONDS)
        return self._code_at(secret, counter)

    def verify(self, secret: str, code: str, window: int = 1) -> bool:
        """Accepts the current period ±`window` (clock-drift tolerance)."""
        if not secret or not code:
            return False
        counter = int(time.time() // self.PERIOD_SECONDS)
        return any(
            hmac.compare_digest(self._code_at(secret, counter + drift), code.strip())
            for drift in range(-window, window + 1)
        )


class SlidingWindowRateLimiter:
    """In-memory per-key sliding window. Swap for Redis behind same interface
    when running multiple instances."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.time()

    def allow(self, key: str) -> bool:
        now = time.time()
        floor = now - self._window
        with self._lock:
            self._sweep(floor, now)
            bucket = [t for t in self._hits.get(key, []) if t > floor]
            if len(bucket) >= self._max:
                self._hits[key] = bucket
                return False
            bucket.append(now)
            self._hits[key] = bucket
            return True

    def _sweep(self, floor: float, now: float) -> None:
        """Drops idle keys once per window so churning client IPs can't grow
        the dict unboundedly. Caller holds the lock."""
        if now - self._last_sweep < self._window:
            return
        self._hits = {key: alive for key, hits in self._hits.items()
                      if (alive := [t for t in hits if t > floor])}
        self._last_sweep = now
