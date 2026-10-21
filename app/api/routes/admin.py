"""Administrative routes for user and API-key provisioning."""

import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_token
from app.database.models import ApiKey, User
from app.database.session import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["administration"])


class CreateApiKeyRequest(BaseModel):
    """Describe the user and label for a newly generated API key."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    key_name: str = Field(
        default="iPhone Health Export",
        min_length=1,
        max_length=100,
    )


@router.post(
    "/api-keys",
    dependencies=[Depends(require_admin_token)],
    status_code=201,
)
def create_api_key(
    request: CreateApiKeyRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a user if needed and return a new API key once."""
    
    email = request.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            name=request.name.strip(),
            email=email,
        )
        db.add(user)
        db.flush()

    raw_key = secrets.token_urlsafe(32)
    api_key = ApiKey(
        user_id=user.id,
        name=request.key_name.strip(),
        key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
    )
    db.add(api_key)
    try:
        db.commit()
        db.refresh(api_key)
    except Exception:
        db.rollback()
        raise

    return {
        "message": "API key created. Store it securely; it will not be shown again.",
        "user_id": str(user.id),
        "email": user.email,
        "key_id": str(api_key.id),
        "key_name": api_key.name,
        "api_key": raw_key,
    }
