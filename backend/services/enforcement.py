"""
Enforcement service — task locking, violation creation, data integrity checks.
Controls rep behavior and ensures process compliance.
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import Lead, Task, Appointment, User, Violation, Project


# =================================================================
# LEAD LOCKING
# =================================================================

def check_lead_lock(db: Session, lead_id: int) -> dict:
    """Check if a lead is locked due to pending tasks."""
    open_tasks = (
        db.query(Task)
        .filter(
            Task.lead_id == lead_id,
            Task.status.in_(["pending", "overdue"]),
        )
        .all()
    )
    is_locked = len(open_tasks) > 0
    return {
        "is_locked": is_locked,
        "open_task_count": len(open_tasks),
        "tasks": [
            {"id": t.id, "title": t.title, "status": t.status, "due_date": str(t.due_date) if t.due_date else None}
            for t in open_tasks
        ],
    }


def enforce_lead_lock(db: Session, lead_id: int):
    """Update lead's is_locked flag based on pending tasks."""
    lock_info = check_lead_lock(db, lead_id)
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if lead:
        lead.is_locked = lock_info["is_locked"]
        db.flush()
    return lock_info


# =================================================================
# VIOLATION CREATION
# =================================================================

VIOLATION_TYPES = {
    "missed_task": {"severity": "serious", "desc": "Task was not completed by due date"},
    "overdue_followup": {"severity": "warning", "desc": "Follow-up not completed on time"},
    "no_update_after_appt": {"severity": "serious", "desc": "No outcome recorded after appointment"},
    "missing_notes": {"severity": "warning", "desc": "Required notes not provided"},
    "skipped_task": {"severity": "critical", "desc": "Attempted to bypass required task"},
}


def create_violation(
    db: Session,
    rep_id: int,
    violation_type: str,
    description: str,
    lead_id: int = None,
    appointment_id: int = None,
    task_id: int = None,
):
    """Create a violation record for a rep."""
    vtype = VIOLATION_TYPES.get(violation_type, {"severity": "warning"})
    violation = Violation(
        rep_id=rep_id,
        violation_type=violation_type,
        severity=vtype["severity"],
        lead_id=lead_id,
        appointment_id=appointment_id,
        task_id=task_id,
        description=description,
    )
    db.add(violation)
    db.flush()
    return violation


# =================================================================
# AUTOMATED VIOLATION SCANNING
# =================================================================

def scan_overdue_tasks(db: Session):
    """Scan for overdue tasks and create violations for each."""
    now = datetime.utcnow()
    overdue_tasks = (
        db.query(Task)
        .filter(
            Task.status == "overdue",
            Task.assigned_to.isnot(None),
        )
        .all()
    )

    new_violations = 0
    for task in overdue_tasks:
        # Check if violation already exists for this task
        existing = (
            db.query(Violation)
            .filter(
                Violation.task_id == task.id,
                Violation.violation_type == "missed_task",
            )
            .first()
        )
        if not existing:
            create_violation(
                db=db,
                rep_id=task.assigned_to,
                violation_type="missed_task",
                description=f"Task '{task.title}' overdue since {task.due_date}",
                lead_id=task.lead_id,
                task_id=task.id,
            )
            new_violations += 1

    return new_violations


def scan_stale_appointments(db: Session, hours_threshold: int = 4):
    """Find completed appointments with no outcome recorded."""
    cutoff = datetime.utcnow() - timedelta(hours=hours_threshold)
    stale = (
        db.query(Appointment)
        .filter(
            Appointment.appointment_status == "arrived",
            Appointment.created_at < cutoff,
            Appointment.outcome.is_(None),
        )
        .all()
    )

    new_violations = 0
    for appt in stale:
        existing = (
            db.query(Violation)
            .filter(
                Violation.appointment_id == appt.id,
                Violation.violation_type == "no_update_after_appt",
            )
            .first()
        )
        if not existing and appt.assigned_rep_id:
            create_violation(
                db=db,
                rep_id=appt.assigned_rep_id,
                violation_type="no_update_after_appt",
                description=f"No outcome recorded for appointment #{appt.id} (arrived {hours_threshold}+ hours ago)",
                lead_id=appt.lead_id,
                appointment_id=appt.id,
            )
            new_violations += 1

    return new_violations


def scan_overdue_followups(db: Session):
    """Find leads with overdue follow-up dates."""
    now = datetime.utcnow()
    overdue_leads = (
        db.query(Lead)
        .filter(
            Lead.follow_up_required == True,
            Lead.next_follow_up_date < now,
            Lead.assigned_rep_id.isnot(None),
            Lead.is_archived == False,
        )
        .all()
    )

    new_violations = 0
    for lead in overdue_leads:
        existing = (
            db.query(Violation)
            .filter(
                Violation.lead_id == lead.id,
                Violation.violation_type == "overdue_followup",
                Violation.created_at > now - timedelta(days=1),
            )
            .first()
        )
        if not existing:
            create_violation(
                db=db,
                rep_id=lead.assigned_rep_id,
                violation_type="overdue_followup",
                description=f"Follow-up overdue for {lead.first_name} {lead.last_name} (due {lead.next_follow_up_date})",
                lead_id=lead.id,
            )
            new_violations += 1

    return new_violations


def run_all_scans(db: Session):
    """Run all violation scans. Call periodically or on dashboard load."""
    results = {
        "overdue_tasks": scan_overdue_tasks(db),
        "stale_appointments": scan_stale_appointments(db),
        "overdue_followups": scan_overdue_followups(db),
    }
    db.commit()
    return results


# =================================================================
# REP PERFORMANCE CALCULATIONS (ACCURATE)
# =================================================================

def calculate_rep_performance(db: Session, rep_id: int) -> dict:
    """Calculate accurate performance metrics for a rep."""
    # Total appointments assigned to this rep
    total_appts = (
        db.query(Appointment)
        .filter(Appointment.assigned_rep_id == rep_id)
        .count()
    )

    # Completed appointments (have an outcome)
    completed_appts = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_rep_id == rep_id,
            Appointment.outcome.isnot(None),
        )
        .all()
    )

    # Held = appointments where rep actually met homeowner
    held_outcomes = {"closed", "proposal_presented", "proposal_sent", "follow_up_required", "declined"}
    not_held_outcomes = {"no_show", "not_home", "canceled", "reschedule_requested"}

    held_count = sum(1 for a in completed_appts if a.outcome in held_outcomes)
    not_held_count = sum(1 for a in completed_appts if a.outcome in not_held_outcomes)
    closed_count = sum(1 for a in completed_appts if a.outcome == "closed")

    total_completed = held_count + not_held_count
    held_rate = (held_count / total_completed * 100) if total_completed > 0 else 0
    close_rate = (closed_count / held_count * 100) if held_count > 0 else 0

    # Revenue from closed deals — ONLY from Project.deal_value (real contracts)
    closed_projects = (
        db.query(Project)
        .filter(
            Project.closer_id == rep_id,
            Project.deal_value != None,
            Project.deal_value > 0,
        )
        .all()
    )
    total_revenue = sum(p.deal_value for p in closed_projects)

    # Open tasks
    open_tasks = (
        db.query(Task)
        .filter(
            Task.assigned_to == rep_id,
            Task.status.in_(["pending", "overdue"]),
        )
        .count()
    )

    overdue_tasks = (
        db.query(Task)
        .filter(Task.assigned_to == rep_id, Task.status == "overdue")
        .count()
    )

    # Violations
    violation_count = (
        db.query(Violation)
        .filter(Violation.rep_id == rep_id, Violation.acknowledged == False)
        .count()
    )

    total_violations = (
        db.query(Violation)
        .filter(Violation.rep_id == rep_id)
        .count()
    )

    # Active leads
    active_leads = (
        db.query(Lead)
        .filter(
            Lead.assigned_rep_id == rep_id,
            Lead.is_archived == False,
            Lead.deal_status.notin_(["closed_won", "closed_lost"]),
        )
        .count()
    )

    return {
        "rep_id": rep_id,
        "total_appointments": total_appts,
        "completed_appointments": total_completed,
        "held_count": held_count,
        "not_held_count": not_held_count,
        "closed_count": closed_count,
        "held_rate": round(held_rate, 1),
        "close_rate": round(close_rate, 1),
        "total_revenue": total_revenue,
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "active_violations": violation_count,
        "total_violations": total_violations,
        "active_leads": active_leads,
    }


# =================================================================
# DISPATCH INTELLIGENCE
# =================================================================

def recommend_rep(db: Session, lead_id: int) -> list:
    """Recommend reps for a lead based on workload + performance."""
    reps = db.query(User).filter(User.role == "rep", User.is_active == True).all()
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    scored = []
    for rep in reps:
        perf = calculate_rep_performance(db, rep.id)

        # Score: higher is better
        # Factors: close rate (weight 3), held rate (weight 2), low workload (weight 2), low violations (weight 1)
        workload_score = max(0, 100 - perf["active_leads"] * 10 - perf["open_tasks"] * 15)
        violation_penalty = perf["active_violations"] * 20
        performance_score = perf["close_rate"] * 3 + perf["held_rate"] * 2 + workload_score * 2 - violation_penalty

        scored.append({
            "rep_id": rep.id,
            "rep_name": rep.full_name,
            "email": rep.email,
            "score": round(performance_score, 1),
            "close_rate": perf["close_rate"],
            "held_rate": perf["held_rate"],
            "active_leads": perf["active_leads"],
            "open_tasks": perf["open_tasks"],
            "overdue_tasks": perf["overdue_tasks"],
            "violations": perf["active_violations"],
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:5]  # Top 5 recommendations
