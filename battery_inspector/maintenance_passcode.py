"""Screen gating for ML Training and Settings.

**This is not a security control, and it must not be described as one.** It is
an operational speed bump: it stops an operator wandering into a screen where a
wrong entry changes what the station inspects. Anyone with the workstation's
file system, the installer, or this source can bypass it in a minute, and the
passcode itself is short enough to guess. Treat the Windows account, the
station's physical access, and the audit log as the real controls.

What it does buy, and the reason it is worth having:

* it makes entering a maintenance screen a deliberate act rather than a
  mis-tap on a touchscreen;
* every unlock, refusal, and passcode change is written to the audit log, so
  "who opened Settings before that recipe changed" has an answer.

The passcode is stored salted and hashed rather than in plain text -- not
because the hash resists attack (a four-character passcode does not), but so
that a config file opened over someone's shoulder, pasted into a ticket, or
carried in a station backup does not simply display it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# Iterations are deliberately modest. This runs on a station when a technician
# taps a screen, and no iteration count makes a four-character passcode strong.
ITERATIONS = 200_000
DEFAULT_PASSCODE = "PP26"


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_passcode(passcode: str, salt: str) -> str:
    """Derive the stored digest for a passcode and salt."""

    return hashlib.pbkdf2_hmac(
        "sha256",
        passcode.encode("utf-8"),
        salt.encode("utf-8"),
        ITERATIONS,
    ).hex()


def verify(passcode: str, salt: str, digest: str) -> bool:
    """Constant-time comparison, so the check does not leak by timing."""

    if not digest:
        # No passcode has been configured. The caller decides what that means;
        # this module never silently grants access.
        return False
    return hmac.compare_digest(hash_passcode(passcode, salt), digest)


def default_credentials() -> tuple[str, str]:
    """Salt and digest for the shipped default passcode.

    Generated fresh for each station rather than baked in, so two stations do
    not share a stored digest.
    """

    salt = new_salt()
    return salt, hash_passcode(DEFAULT_PASSCODE, salt)
