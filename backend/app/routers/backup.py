"""Backup and restore for the app's databases.

Restore is a POST with an explicit id and it takes a safety snapshot first — it is the only
destructive operation in this router, and the only one that cannot be undone by repeating it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import backup as backup_service
from ..services.backup import BackupError

router = APIRouter(prefix="/backup", tags=["backup"])


class CreateRequest(BaseModel):
    label: str = ""


@router.get("")
def list_backups() -> dict:
    return {"backups": backup_service.listing(), "directory": str(backup_service.BACKUP_DIR)}


@router.post("")
def create_backup(body: CreateRequest) -> dict:
    try:
        return {"backup": backup_service.create(body.label)}
    except BackupError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None


@router.delete("/{backup_id}")
def delete_backup(backup_id: str) -> dict:
    try:
        backup_service.delete(backup_id)
    except BackupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from None
    return {"deleted": backup_id}


@router.post("/{backup_id}/restore")
def restore_backup(backup_id: str) -> dict:
    try:
        return backup_service.restore(backup_id)
    except BackupError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
