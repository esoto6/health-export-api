"""Unauthenticated system-health routes."""

from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    """Report that the API process is available."""

    return {"status": "ok"}
