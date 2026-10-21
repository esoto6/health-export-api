"""Bearer-key authentication for user-owned health data."""

import hashlib

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import ApiKey, User
from app.database.session import get_db

user_bearer = HTTPBearer(
    scheme_name="UserApiKey",
    description="Enter your user API key",
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(user_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate an active API key and return its owning user."""

    supplied = credentials.credentials
    key_hash = hashlib.sha256(supplied.encode("utf-8")).hexdigest()

    api_key = db.scalar(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
        )
    )

    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    user = db.get(User, api_key.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="API key user not found")

    return user
