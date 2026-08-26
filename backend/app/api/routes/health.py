import os
from datetime import datetime
from fastapi import APIRouter
from app.schemas.pydantic_models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Liveness check — returns status and UTC timestamp."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.utcnow(),
        environment=os.getenv("ENVIRONMENT", "development"),
    )
