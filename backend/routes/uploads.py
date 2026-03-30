"""File upload management routes for leads, appointments, and projects."""
from datetime import datetime
from typing import List, Optional
import base64
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import FileUpload, Lead, Appointment, Project, User
from app.auth import get_current_user

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


class FileUploadCreate(BaseModel):
    """Create file upload request model."""
    lead_id: Optional[int] = None
    appointment_id: Optional[int] = None
    project_id: Optional[int] = None
    file_name: str
    file_type: str  # photo, voice_memo, document
    file_data: str  # base64-encoded file content
    file_size: Optional[int] = None


class FileUploadResponse(BaseModel):
    """File upload response model."""
    id: int
    lead_id: Optional[int]
    appointment_id: Optional[int]
    project_id: Optional[int]
    uploaded_by: int
    file_name: str
    file_type: str
    file_url: str
    file_size: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("", response_model=FileUploadResponse)
def create_file_upload(
    upload_data: FileUploadCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload a file (photo, voice memo, document) for a lead, appointment, or project.

    At least one of lead_id, appointment_id, or project_id must be provided.
    The file_data should be a base64-encoded string.

    Args:
        upload_data: File upload data with base64 content
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created file upload record

    Raises:
        HTTPException: 400 if no entity ID or invalid base64, 404 if entity not found
    """
    # Validate that at least one entity is specified
    if not any([upload_data.lead_id, upload_data.appointment_id, upload_data.project_id]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide at least one of: lead_id, appointment_id, or project_id",
        )

    # Validate entity exists if provided
    if upload_data.lead_id:
        lead = db.query(Lead).filter(Lead.id == upload_data.lead_id).first()
        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found",
            )

    if upload_data.appointment_id:
        appointment = db.query(Appointment).filter(Appointment.id == upload_data.appointment_id).first()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found",
            )

    if upload_data.project_id:
        project = db.query(Project).filter(Project.id == upload_data.project_id).first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

    # Validate base64 encoding
    try:
        base64.b64decode(upload_data.file_data, validate=True)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64-encoded file_data",
        )

    # Construct data URI
    file_uri = f"data:application/octet-stream;base64,{upload_data.file_data}"

    # Create file upload record
    file_upload = FileUpload(
        lead_id=upload_data.lead_id,
        appointment_id=upload_data.appointment_id,
        project_id=upload_data.project_id,
        uploaded_by=current_user.id,
        file_name=upload_data.file_name,
        file_type=upload_data.file_type,
        file_url=file_uri,
        file_size=upload_data.file_size,
    )

    db.add(file_upload)
    db.commit()
    db.refresh(file_upload)

    return file_upload


@router.get("", response_model=List[FileUploadResponse])
def list_file_uploads(
    lead_id: Optional[int] = Query(None),
    appointment_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    file_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List file uploads for a specific lead, appointment, or project.

    Must provide exactly one of: lead_id, appointment_id, or project_id.
    Optionally filter by file_type (photo, voice_memo, document).

    Args:
        lead_id: Filter by lead ID
        appointment_id: Filter by appointment ID
        project_id: Filter by project ID
        file_type: Optional filter by file type
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of file uploads for the entity

    Raises:
        HTTPException: 400 if multiple or no entity filters provided
    """
    # Ensure exactly one entity filter is provided
    filters = [lead_id, appointment_id, project_id]
    provided_filters = sum(1 for f in filters if f is not None)

    if provided_filters == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide exactly one of: lead_id, appointment_id, or project_id",
        )

    if provided_filters > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide only one of: lead_id, appointment_id, or project_id",
        )

    # Query file uploads
    query = db.query(FileUpload)

    if lead_id:
        query = query.filter(FileUpload.lead_id == lead_id)
    elif appointment_id:
        query = query.filter(FileUpload.appointment_id == appointment_id)
    elif project_id:
        query = query.filter(FileUpload.project_id == project_id)

    if file_type:
        query = query.filter(FileUpload.file_type == file_type)

    uploads = query.order_by(FileUpload.created_at.asc()).all()

    return uploads
