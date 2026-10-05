"""RFC 6238 time-based one-time passwords and one-time recovery codes. Standard library only."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def code_for_step(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return f"{number:0{DIGITS}d}"


def verify(secret: str, code: str, last_step: int = 0, window: int = 1, now: float | None = None):
    """Return the matching time step, or None. A step at or before last_step is rejected so a code works once."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(current - window, current + window + 1):
        if step > last_step and hmac.compare_digest(code_for_step(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str, issuer: str = "MC Server Manager") -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}"


def normalize_recovery(code: str) -> str:
    return "".join(ch for ch in str(code).lower() if ch.isalnum())


def hash_recovery(code: str) -> str:
    return hashlib.sha256(normalize_recovery(code).encode("ascii", "ignore")).hexdigest()


def new_recovery_codes(count: int = 8):
    """Return (plain codes to show once, hashes to store)."""
    plain = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]
    return plain, [hash_recovery(code) for code in plain]


def consume_recovery(code: str, hashes: list):
    """Return the remaining hashes if the code was valid, otherwise None."""
    digest = hash_recovery(code)
    for index, stored in enumerate(hashes):
        if hmac.compare_digest(stored, digest):
            return hashes[:index] + hashes[index + 1:]
    return None
