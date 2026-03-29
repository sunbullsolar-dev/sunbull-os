"""Task management routes — the enforcement layer API.

Tasks are auto-created by the outcome engine but can also be queried,
completed, and monitored through these endpoints.
"""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Task, Lead, User, Appointment
from app.auth import get_current_user, require_role
from services.outcome_engine import mark_overdue_tasks

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCompleteRequest(BaseModel):
    completion_notes: str


class TaskResponse(BaseModel):
    id: int
    lead_id: int
    appointment_id: Optional[int]
    assigned_to: int
    task_type: str
    title: str
    description: Optional[str]
    due_date: str
    status: str
    source_outcome: Optional[str]
    notes: Optional[str]
    completion_notes: Optional[str]
    completed_at: Optional[str]
    created_at: str

    # Joined data
    lead_name: Optional[str] = None
    assignee_name: Optional[str] = None


@router.get("")
def list_tasks(
    status: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    assigned_to: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List tasks. Reps see only their tasks. Admins see all.
    Automatically marks overdue tasks before returning.
    """
    # Auto-mark overdue tasks
    mark_overdue_tasks(db)

    query = db.query(Task)

    # Role-based filtering
    if current_user.role == "rep":
        query = query.filter(Task.assigned_to == current_user.id)
    elif assigned_to:
        query = query.filter(Task.assigned_to == assigned_to)

    if status:
        query = query.filter(Task.status == status)
    if task_type:
        query = query.filter(Task.task_type == task_type)

    tasks = query.order_by(Task.due_date.asc()).all()

    result = []
    for task in tasks:
        lead = db.query(Lead).filter(Lead.id == task.lead_id).first()
        assignee = db.query(User).filter(User.id == task.assigned_to).first()
        result.append({
            "id": task.id,
            "lead_id": task.lead_id,
            "appointment_id": task.appointment_id,
            "assigned_to": task.assigned_to,
            "task_type": task.task_type,
            "title": task.title,
            "description": task.description,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "status": task.status,
            "source_outcome": task.source_outcome,
            "notes": task.notes,
            "completion_notes": task.completion_notes,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
            "assignee_name": assignee.full_name if assignee else None,
        })

    return result


@router.get("/summary")
def task_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get task summary counts for dashboard display.
    Returns pending, overdue, due_today, completed_today counts.
    """
    mark_overdue_tasks(db)

    query = db.query(Task)
    if current_user.role == "rep":
        query = query.filter(Task.assigned_to == current_user.id)

    all_tasks = query.all()

    today = datetime.utcnow().date()
    pending = 0
    overdue = 0
    due_today = 0
    completed_today = 0

    for t in all_tasks:
        if t.status == "pending":
            pending += 1
            if t.due_date and t.due_date.date() == today:
                due_today += 1
        elif t.status == "overdue":
            overdue += 1
        elif t.status == "completed" and t.completed_at and t.completed_at.date() == today:
            completed_today += 1

    return {
        "pending": pending,
        "overdue": overdue,
        "due_today": due_today,
        "completed_today": completed_today,
        "total_open": pending + overdue,
    }


@router.post("/{task_id}/complete")
def complete_task(
    task_id: int,
    data: TaskCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Complete a task. Requires completion_notes.
    Only the assigned rep or an admin can complete a task.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if current_user.role == "rep" and task.assigned_to != current_user.id:
        raise HTTPException(status_code=403, detail="Not your task")

    if task.status == "completed":
        raise HTTPException(status_code=400, detail="Task already completed")

    if not data.completion_notes or not data.completion_notes.strip():
        raise HTTPException(status_code=400, detail="Completion notes required")

    task.status = "completed"
    task.completed_at = datetime.utcnow()
    task.completion_notes = data.completion_notes

    db.commit()
    db.refresh(task)

    return {
        "id": task.id,
        "status": task.status,
        "completed_at": task.completed_at.isoformat(),
        "completion_notes": task.completion_notes,
    }


@router.put("/{task_id}/reassign")
def reassign_task(
    task_id: int,
    rep_id: int = Query(...),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin-only: reassign a task to a different rep."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    rep = db.query(User).filter(User.id == rep_id).first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")

    old_assignee = task.assigned_to
    task.assigned_to = rep_id

    # If this is a rehash task, also update lead.rehash_rep_id
    if task.task_type == "rehash":
        lead = db.query(Lead).filter(Lead.id == task.lead_id).first()
        if lead:
            lead.rehash_rep_id = rep_id

    db.commit()

    return {
        "id": task.id,
        "assigned_to": task.assigned_to,
        "previous_assignee": old_assignee,
    }
