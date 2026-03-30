"""Comment management routes — universal update feed for leads, appointments, and projects."""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Comment, Lead, Appointment, Project, User
from app.auth import get_current_user

router = APIRouter(prefix="/api/comments", tags=["comments"])


class CommentCreate(BaseModel):
    """Create comment request model."""
    lead_id: Optional[int] = None
    appointment_id: Optional[int] = None
    project_id: Optional[int] = None
    body: str
    comment_type: str = "note"


class CommentResponse(BaseModel):
    """Comment response model."""
    id: int
    lead_id: Optional[int]
    appointment_id: Optional[int]
    project_id: Optional[int]
    user_id: int
    body: str
    comment_type: str
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("", response_model=CommentResponse)
def create_comment(
    comment_data: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a comment on a lead, appointment, or project.

    At least one of lead_id, appointment_id, or project_id must be provided.

    Args:
        comment_data: Comment creation data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created comment

    Raises:
        HTTPException: 400 if no entity ID provided, 404 if entity not found
    """
    # Validate that at least one entity is specified
    if not any([comment_data.lead_id, comment_data.appointment_id, comment_data.project_id]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide at least one of: lead_id, appointment_id, or project_id",
        )

    # Validate entity exists if provided
    if comment_data.lead_id:
        lead = db.query(Lead).filter(Lead.id == comment_data.lead_id).first()
        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found",
            )

    if comment_data.appointment_id:
        appointment = db.query(Appointment).filter(Appointment.id == comment_data.appointment_id).first()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found",
            )

    if comment_data.project_id:
        project = db.query(Project).filter(Project.id == comment_data.project_id).first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

    # Create comment
    comment = Comment(
        lead_id=comment_data.lead_id,
        appointment_id=comment_data.appointment_id,
        project_id=comment_data.project_id,
        user_id=current_user.id,
        body=comment_data.body,
        comment_type=comment_data.comment_type,
    )

    db.add(comment)
    db.commit()
    db.refresh(comment)

    return comment


@router.get("", response_model=List[CommentResponse])
def list_comments(
    lead_id: Optional[int] = Query(None),
    appointment_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List comments for a specific lead, appointment, or project.

    Must provide exactly one of: lead_id, appointment_id, or project_id.

    Args:
        lead_id: Filter by lead ID
        appointment_id: Filter by appointment ID
        project_id: Filter by project ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of comments for the entity

    Raises:
        HTTPException: 400 if multiple or no filters provided
    """
    # Ensure exactly one filter is provided
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

    # Query comments
    query = db.query(Comment)

    if lead_id:
        query = query.filter(Comment.lead_id == lead_id)
    elif appointment_id:
        query = query.filter(Comment.appointment_id == appointment_id)
    elif project_id:
        query = query.filter(Comment.project_id == project_id)

    comments = query.order_by(Comment.created_at.asc()).all()

    return comments
