"""Security primitives: password hashing, signed tokens, rate limiting.

Stdlib only (hashlib/hmac) — no weak homemade crypto: PBKDF2-HMAC-SHA256 with
per-password random salt, constant-time comparison, HMAC-signed expiring tokens.
(TOTP two-factor authentication ships with PyWebFW Pro.)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass

from pywebfw.core.exceptions import AuthenticationError


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
    """RFC 6238 TOTP (Time-based One-Time Password) using stdlib only.

    Compatible with Google Authenticator / Authy / any TOTP app.
    30-second window, 6-digit codes, SHA-1 (TOTP standard).
    """

    def __init__(self, digits: int = 6, step: int = 30, window: int = 1) -> None:
        self._digits = digits
        self._step = step
        self._window = window  # number of steps ±1 accepted for clock skew

    # ------------------------------------------------------------------
    def generate_secret(self) -> str:
        """Return a random 20-byte Base32-encoded secret (no padding)."""
        raw = secrets.token_bytes(20)
        return base64.b32encode(raw).decode().rstrip("=")

    def _hotp(self, secret: str, counter: int) -> str:
        """Compute HOTP value for given secret and counter."""
        # Pad Base32 to multiple of 8
        padded = secret.upper() + "=" * (-len(secret) % 8)
        try:
            key = base64.b32decode(padded)
        except Exception:
            return ""
        msg = counter.to_bytes(8, "big")
        h = hmac.new(key, msg, hashlib.sha1).digest()
        offset = h[-1] & 0x0F
        code_int = (
            ((h[offset] & 0x7F) << 24)
            | (h[offset + 1] << 16)
            | (h[offset + 2] << 8)
            | h[offset + 3]
        ) % (10 ** self._digits)
        return str(code_int).zfill(self._digits)

    def current_code(self, secret: str) -> str:
        """Return the current TOTP code."""
        counter = int(time.time()) // self._step
        return self._hotp(secret, counter)

    def verify(self, secret: str, code: str) -> bool:
        """Verify a TOTP code, accepting ±window steps for clock skew."""
        if not secret or not code:
            return False
        counter = int(time.time()) // self._step
        for delta in range(-self._window, self._window + 1):
            if hmac.compare_digest(self._hotp(secret, counter + delta), code):
                return True
        return False

    def provisioning_uri(self, secret: str, account: str,
                         issuer: str = "PyWebFW") -> str:
        """Return an otpauth:// URI for QR-code provisioning."""
        from urllib.parse import quote
        return (
            f"otpauth://totp/{quote(issuer)}:{quote(account)}"
            f"?secret={secret}&issuer={quote(issuer)}&digits={self._digits}"
            f"&period={self._step}"
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
