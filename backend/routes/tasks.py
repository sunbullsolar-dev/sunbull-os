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
from app.models import Task, Lead, User, Appointment, Violation
from app.auth import get_current_user, require_role
from services.outcome_engine import mark_overdue_tasks
from services.enforcement_engine import (
    get_work_queue, check_lead_locked, run_enforcement_check,
    get_rep_violations,
)

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
    lead_id: Optional[int] = Query(None),
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

    if lead_id:
        query = query.filter(Task.lead_id == lead_id)
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

    # Update lead lock status — check if lead has remaining open tasks
    lead_unlocked = None
    if task.lead_id:
        remaining = (
            db.query(Task)
            .filter(
                Task.lead_id == task.lead_id,
                Task.id != task.id,
                Task.status.in_(["pending", "overdue"]),
            )
            .count()
        )
        lead = db.query(Lead).filter(Lead.id == task.lead_id).first()
        if lead:
            lead.is_locked = remaining > 0
            lead_unlocked = remaining == 0

    db.commit()
    db.refresh(task)

    return {
        "id": task.id,
        "status": task.status,
        "completed_at": task.completed_at.isoformat(),
        "completion_notes": task.completion_notes,
        "lead_unlocked": lead_unlocked,
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


# ============================================================================
# ENFORCEMENT ENDPOINTS
# ============================================================================

@router.get("/work-queue")
def get_rep_work_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the prioritized daily work queue for the current rep.
    Returns: overdue (RED), due_today (AMBER), upcoming (GREEN), appointments.
    Also returns overall status: red/amber/green and violation counts.
    """
    rep_id = current_user.id
    if current_user.role == "admin":
        # Admins can view but they get an empty queue
        return {
            "overdue": {"count": 0, "tasks": []},
            "due_today": {"count": 0, "tasks": []},
            "upcoming": {"count": 0, "tasks": []},
            "appointments": {"count": 0, "items": []},
            "violations": {"active": 0, "total": 0},
            "status": "green",
        }
    return get_work_queue(db, rep_id)


@router.get("/lead-lock/{lead_id}")
def check_lead_lock(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Check if a lead is locked due to incomplete tasks.
    Returns: {"locked": bool, "reason": str, "blocking_tasks": [...]}
    """
    return check_lead_locked(db, lead_id)


@router.get("/violations")
def list_violations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get violations for the current rep (reps see own, admins see all).
    """
    if current_user.role == "rep":
        violations = get_rep_violations(db, current_user.id, unacknowledged_only=False)
    else:
        violations = (
            db.query(Violation)
            .order_by(Violation.created_at.desc())
            .limit(200)
            .all()
        )

    result = []
    for v in violations:
        rep = db.query(User).filter(User.id == v.rep_id).first()
        lead = db.query(Lead).filter(Lead.id == v.lead_id).first() if v.lead_id else None
        result.append({
            "id": v.id,
            "rep_id": v.rep_id,
            "rep_name": rep.full_name if rep else None,
            "violation_type": v.violation_type,
            "severity": v.severity,
            "description": v.description,
            "lead_id": v.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
            "task_id": v.task_id,
            "acknowledged": v.acknowledged,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })

    return result


@router.post("/violations/{violation_id}/acknowledge")
def acknowledge_violation(
    violation_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin-only: acknowledge a violation."""
    violation = db.query(Violation).filter(Violation.id == violation_id).first()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")

    violation.acknowledged = True
    violation.acknowledged_by = current_user.id
    violation.acknowledged_at = datetime.utcnow()

    db.commit()
    return {"id": violation.id, "acknowledged": True}
