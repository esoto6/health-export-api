"""Shared FastAPI dependencies for administrator authentication."""

import hmac

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

admin_bearer = HTTPBearer(
    scheme_name="AdminBearer",
    description="Enter your administrator API token",
)


def require_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(admin_bearer),
) -> None:
    """Restrict API-key creation to an administrator."""
    
    expected = settings.admin_api_token
    if not expected or len(expected) < 32:
        raise HTTPException(
            status_code=503,
            detail="API-key administration is not configured",
        )
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=403, detail="Invalid administrator token")
