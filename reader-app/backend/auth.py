"""Username/password authentication and bearer-token dependencies."""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from scripts.repo_paths import DATA_DIR

from schemas import LoginIn, RegisterIn, TokenOut, UserOut

USERS_PATH = DATA_DIR / "users.json"
SECRET_PATH = DATA_DIR / ".jwt_secret"
JWT_ALGORITHM = "HS256"
TOKEN_LIFETIME = timedelta(days=30)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_passwords = CryptContext(schemes=["bcrypt"], deprecated="auto")
_store_lock = threading.Lock()
_username_re = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_bearer = HTTPBearer(auto_error=False)


def _load_users() -> dict[str, dict]:
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _atomic_json_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _jwt_secret() -> str:
    configured = os.environ.get("NOVEL_JWT_SECRET", "").strip()
    if configured:
        return configured
    try:
        secret = SECRET_PATH.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    except FileNotFoundError:
        pass

    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    try:
        # Exclusive creation makes concurrent first requests converge on one secret.
        fd = os.open(SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(generated)
    return generated


def hash_password(password: str) -> str:
    return _passwords.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _passwords.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


def create_token(username: str, *, expires: timedelta = TOKEN_LIFETIME) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + expires}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
        if not isinstance(username, str) or not username:
            raise JWTError("missing subject")
        return username
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _user_for_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials is None:
        return None
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required")
    username = decode_token(credentials.credentials)
    with _store_lock:
        if username not in _load_users():
            raise HTTPException(status_code=401, detail="User no longer exists")
    return username


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    username = _user_for_token(credentials)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str | None:
    return _user_for_token(credentials)


def require_admin(username: str = Depends(current_user)) -> str:
    if username != "lingwei":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return username


def _validate_credentials(username: str, password: str) -> tuple[str, str]:
    username = username.strip()
    if not _username_re.fullmatch(username):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3-64 letters, numbers, dots, dashes, or underscores",
        )
    if not (8 <= len(password) <= 128):
        raise HTTPException(status_code=422, detail="Password must be 8-128 characters")
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password is too long for bcrypt")
    return username, password


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn) -> TokenOut:
    username, password = _validate_credentials(body.username, body.password)
    with _store_lock:
        users = _load_users()
        if username in users:
            raise HTTPException(status_code=409, detail="Username already exists")
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        users[username] = {
            "password_hash": hash_password(password),
            "created": created,
        }
        _atomic_json_write(USERS_PATH, users)
    user = UserOut(username=username, created=created)
    return TokenOut(access_token=create_token(username), user=user)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn) -> TokenOut:
    username = body.username.strip()
    with _store_lock:
        record = _load_users().get(username)
    if not record or not verify_password(body.password, record.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user = UserOut(username=username, created=record.get("created", ""))
    return TokenOut(access_token=create_token(username), user=user)


@router.get("/me", response_model=UserOut)
def me(username: str = Depends(current_user)) -> UserOut:
    with _store_lock:
        record = _load_users().get(username, {})
    return UserOut(username=username, created=record.get("created", ""))
