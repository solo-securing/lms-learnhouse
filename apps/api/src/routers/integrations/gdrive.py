"""Google Drive integration status (instance-wide, read-only)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.db.users import AnonymousUser
from src.security.auth import get_current_user
from src.services.integrations.gdrive.readiness import get_status

router = APIRouter()


class GDriveStatusRead(BaseModel):
    enabled: bool
    ready: bool
    reason: str | None


@router.get(
    "/status",
    response_model=GDriveStatusRead,
    summary="Google Drive integration readiness",
    description=(
        "Whether video activities can be stored on the operator's Google Drive right now. "
        "Used by the create-video dialog to decide whether to offer the storage choice. "
        "Requires a signed-in user (any role). Never returns the Drive account, file paths or secrets; "
        "the network verdict is cached in-process for up to 300 seconds."
    ),
    responses={
        200: {"description": "Current readiness.", "model": GDriveStatusRead},
        401: {"description": "Authentication required"},
    },
)
async def api_gdrive_status(
    current_user=Depends(get_current_user),
) -> GDriveStatusRead:
    if isinstance(current_user, AnonymousUser):
        raise HTTPException(status_code=401, detail="Not authenticated")
    status = await get_status()
    return GDriveStatusRead(enabled=status.enabled, ready=status.ready, reason=status.reason)
