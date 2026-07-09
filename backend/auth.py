"""Minimal single-user auth: one shared password (APP_PASSWORD env var), a
signed bearer token with an expiry - stdlib hmac only, no extra dependency
or user database needed for a single-manager tool."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi import Header, HTTPException

APP_PASSWORD = os.environ["APP_PASSWORD"]
SECRET_KEY = os.environ.get("SECRET_KEY", APP_PASSWORD).encode("utf-8")
TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hours - re-login once a day of use is fine


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_token() -> str:
    expiry = str(int(time.time()) + TOKEN_TTL_SECONDS)
    signature = _sign(expiry)
    raw = f"{expiry}.{signature}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def verify_token(token: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        expiry, signature = raw.split(".", 1)
    except Exception:
        return False
    if not hmac.compare_digest(signature, _sign(expiry)):
        return False
    return int(expiry) >= int(time.time())


def require_auth(authorization: str = Header(default="")) -> None:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Přihlas se prosím znovu.")
    token = authorization.removeprefix("Bearer ").strip()
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Přihlášení vypršelo, přihlas se prosím znovu.")
