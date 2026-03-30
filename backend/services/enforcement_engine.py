"""
Sunbull OS — Enforcement Engine

This engine FORCES reps to do the right thing. It runs automatically and:
1. Marks overdue tasks as CRITICAL
2. Auto-creates violations when reps miss deadlines
3. Checks lead locking status (can't act on locked leads)
4. Builds the daily work queue (prioritized task list)
5. Blocks actions when prerequisites aren't met

RULES (LAW):
- Overdue task → auto violation (missed_task)
- Task completed late → violation (late_completion)
- No outcome after arrived appointment → violation (no_update)
- Rep has overdue tasks → dashboard goes RED
- Lead with pending tasks → LOCKED (must complete tasks first)
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import (
    Task, Lead, User, Appointment, Violation, Action, AuditLog,
)


# ── VIOLATION AUTO-GENERATION ──────────────────────────────────

def run_enforcement_check(db: Session) -> dict:
    """
    Master enforcement check. Call this periodically or on dashboard load.

    1. Marks overdue tasks
    2. Escalates long-overdue tasks to CRITICAL
    3. Creates violations for missed tasks
    4. Checks for stale arrived appointments with no outcome

    Returns summary of actions taken.
    """
    now = datetime.utcnow()
    actions_taken = {
        "tasks_marked_overdue": 0,
        "tasks_escalated_critical": 0,
        "violations_created": 0,
    }

    # ── 1. Mark pending tasks past due as overdue ────────────────
    overdue_tasks = (
        db.query(Task)
        .filter(
            Task.status == "pending",
            Task.due_date < now,
        )
        .all()
    )
    for task in overdue_tasks:
        task.status = "overdue"
        actions_taken["tasks_marked_overdue"] += 1

    # ── 2. Create violations for overdue tasks (once per task) ───
    overdue_all = (
        db.query(Task)
        .filter(Task.status == "overdue")
        .all()
    )
    for task in overdue_all:
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
            hours_overdue = (now - task.due_date).total_seconds() / 3600
            severity = "critical" if hours_overdue > 48 else "warning"

            violation = Violation(
                rep_id=task.assigned_to,
                violation_type="missed_task",
                severity=severity,
                lead_id=task.lead_id,
                task_id=task.id,
                appointment_id=task.appointment_id,
                description=(
                    f"Task overdue by {int(hours_overdue)}h: {task.title}"
                ),
            )
            db.add(violation)
            actions_taken["violations_created"] += 1

    # ── 3. Check for stale "arrived" appointments (no outcome) ───
    stale_cutoff = now - timedelta(hours=4)
    stale_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.appointment_status == "arrived",
            Appointment.rep_checked_in_at < stale_cutoff,
            Appointment.outcome.is_(None),
        )
        .all()
    )
    for appt in stale_appointments:
        existing = (
            db.query(Violation)
            .filter(
                Violation.appointment_id == appt.id,
                Violation.violation_type == "no_update",
            )
            .first()
        )
        if not existing:
            violation = Violation(
                rep_id=appt.assigned_rep_id,
                violation_type="no_update",
                severity="critical",
                lead_id=appt.lead_id,
                appointment_id=appt.id,
                description=(
                    f"No outcome recorded 4+ hours after arriving at appointment #{appt.id}"
                ),
            )
            db.add(violation)
            actions_taken["violations_created"] += 1

    # ── 4. CONFIRMATION SLA: flag overdue confirmations ───────────
    # Appointments with confirmation_status=pending and created > 2 hours ago
    confirmation_sla_cutoff = now - timedelta(hours=2)
    overdue_confirmations = (
        db.query(Appointment)
        .filter(
            Appointment.confirmation_status.in_(["pending", "confirming"]),
            Appointment.created_at < confirmation_sla_cutoff,
        )
        .all()
    )
    for appt in overdue_confirmations:
        if not getattr(appt, "confirmation_overdue", False):
            try:
                appt.confirmation_overdue = True
                actions_taken.setdefault("confirmations_flagged_overdue", 0)
                actions_taken["confirmations_flagged_overdue"] = actions_taken.get("confirmations_flagged_overdue", 0) + 1
            except Exception:
                pass

    # ── 5. AUTO-REHASH: 3+ failed confirmation attempts ────────────
    three_strike_appts = (
        db.query(Appointment)
        .filter(
            Appointment.confirmation_status.in_(["pending", "confirming"]),
            Appointment.confirmation_attempts_count >= 3,
        )
        .all()
    )
    for appt in three_strike_appts:
        lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
        if lead and lead.deal_status not in ("rehash", "dead", "closed_won", "closed_lost"):
            lead.deal_status = "rehash"
            appt.confirmation_status = "unconfirmed"
            # Create rehash task
            existing_task = (
                db.query(Task)
                .filter(Task.lead_id == lead.id, Task.task_type == "rehash", Task.status.in_(["pending", "overdue"]))
                .first()
            )
            if not existing_task:
                rehash_task = Task(
                    lead_id=lead.id,
                    appointment_id=appt.id,
                    assigned_to=appt.assigned_rep_id,
                    task_type="rehash",
                    title=f"Rehash: {lead.first_name} {lead.last_name} (3 failed confirmations)",
                    due_date=now + timedelta(days=3),
                    status="pending",
                )
                db.add(rehash_task)
            actions_taken.setdefault("auto_rehashed", 0)
            actions_taken["auto_rehashed"] = actions_taken.get("auto_rehashed", 0) + 1

    # ── 6. DISPATCH ACCEPT TIMEOUT: unacknowledged > 30 min ────────
    dispatch_timeout = now - timedelta(minutes=30)
    unacked_appts = (
        db.query(Appointment)
        .filter(
            Appointment.dispatch_status == "assigned",
            Appointment.assigned_at != None,
            Appointment.assigned_at < dispatch_timeout,
        )
        .all()
    )
    for appt in unacked_appts:
        if not getattr(appt, "rep_acknowledged_at", None):
            existing_v = (
                db.query(Violation)
                .filter(
                    Violation.appointment_id == appt.id,
                    Violation.violation_type == "no_update",
                    Violation.description.like("%unacknowledged%"),
                )
                .first()
            )
            if not existing_v:
                mins = int((now - appt.assigned_at).total_seconds() / 60)
                violation = Violation(
                    rep_id=appt.assigned_rep_id,
                    violation_type="no_update",
                    severity="warning",
                    lead_id=appt.lead_id,
                    appointment_id=appt.id,
                    description=f"Dispatch unacknowledged for {mins} minutes",
                )
                db.add(violation)
                actions_taken["violations_created"] += 1

    # ── 7. LATE ARRIVAL DETECTION ──────────────────────────────────
    today = now.date()
    today_appts = (
        db.query(Appointment)
        .filter(
            Appointment.appointment_date == today,
            Appointment.appointment_status.in_(["en_route", "arrived", "completed"]),
        )
        .all()
    )
    for appt in today_appts:
        if appt.appointment_time and appt.rep_checked_in_at:
            from datetime import datetime as _dt, time as _t
            scheduled_dt = _dt.combine(appt.appointment_date, appt.appointment_time)
            if appt.rep_checked_in_at > scheduled_dt + timedelta(minutes=15):
                if not getattr(appt, "rep_late", False):
                    try:
                        appt.rep_late = True
                    except Exception:
                        pass
                    existing_v = (
                        db.query(Violation)
                        .filter(Violation.appointment_id == appt.id, Violation.violation_type.like("%late%"))
                        .first()
                    )
                    if not existing_v:
                        mins_late = int((appt.rep_checked_in_at - scheduled_dt).total_seconds() / 60)
                        violation = Violation(
                            rep_id=appt.assigned_rep_id,
                            violation_type="late_completion",
                            severity="warning" if mins_late < 30 else "critical",
                            lead_id=appt.lead_id,
                            appointment_id=appt.id,
                            description=f"Rep arrived {mins_late} minutes late to appointment",
                        )
                        db.add(violation)
                        actions_taken["violations_created"] += 1

    # ── 8. NO STATUS UPDATE: scheduled today but no action taken ───
    for appt in today_appts:
        if appt.appointment_time:
            from datetime import datetime as _dt2
            scheduled_dt = _dt2.combine(appt.appointment_date, appt.appointment_time)
            if now > scheduled_dt + timedelta(hours=2) and appt.appointment_status == "en_route" and not appt.outcome:
                existing_v = (
                    db.query(Violation)
                    .filter(Violation.appointment_id == appt.id, Violation.violation_type == "no_update")
                    .first()
                )
                if not existing_v:
                    violation = Violation(
                        rep_id=appt.assigned_rep_id,
                        violation_type="no_update",
                        severity="critical",
                        lead_id=appt.lead_id,
                        appointment_id=appt.id,
                        description=f"No arrival/outcome update 2+ hours after scheduled time",
                    )
                    db.add(violation)
                    actions_taken["violations_created"] += 1

    if any(v > 0 for v in actions_taken.values()):
        db.commit()

    return actions_taken


# ── LEAD LOCKING ───────────────────────────────────────────────

def check_lead_locked(db: Session, lead_id: int) -> dict:
    """
    Check if a lead is locked due to pending/overdue tasks.

    Returns:
        {"locked": bool, "reason": str, "blocking_tasks": list}
    """
    open_tasks = (
        db.query(Task)
        .filter(
            Task.lead_id == lead_id,
            Task.status.in_(["pending", "overdue"]),
        )
        .all()
    )

    if not open_tasks:
        return {"locked": False, "reason": None, "blocking_tasks": []}

    overdue = [t for t in open_tasks if t.status == "overdue"]
    blocking = []
    for t in open_tasks:
        blocking.append({
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "task_type": t.task_type,
        })

    if overdue:
        reason = f"{len(overdue)} overdue task(s) must be completed first"
    else:
        reason = f"{len(open_tasks)} pending task(s) must be completed first"

    return {"locked": True, "reason": reason, "blocking_tasks": blocking}


# ── DAILY WORK QUEUE ───────────────────────────────────────────

def get_work_queue(db: Session, rep_id: int) -> dict:
    """
    Build the prioritized daily work queue for a rep.

    Priority order:
    1. OVERDUE tasks (RED) — highest priority, sorted by how late
    2. DUE TODAY tasks (AMBER) — must be done today
    3. UPCOMING tasks (GREEN) — next 7 days
    4. TODAY'S APPOINTMENTS — scheduled for today

    Returns structured queue with counts and items.
    """
    now = datetime.utcnow()
    today = now.date()
    week_out = today + timedelta(days=7)

    # Run enforcement check first
    run_enforcement_check(db)

    # ── OVERDUE TASKS ────────────────────────────────────────────
    overdue_tasks = (
        db.query(Task)
        .filter(
            Task.assigned_to == rep_id,
            Task.status == "overdue",
        )
        .order_by(Task.due_date.asc())  # Most overdue first
        .all()
    )

    # ── DUE TODAY ────────────────────────────────────────────────
    today_start = datetime(today.year, today.month, today.day)
    today_end = today_start + timedelta(days=1)
    due_today_tasks = (
        db.query(Task)
        .filter(
            Task.assigned_to == rep_id,
            Task.status == "pending",
            Task.due_date >= today_start,
            Task.due_date < today_end,
        )
        .order_by(Task.due_date.asc())
        .all()
    )

    # ── UPCOMING (next 7 days, excluding today) ──────────────────
    upcoming_tasks = (
        db.query(Task)
        .filter(
            Task.assigned_to == rep_id,
            Task.status == "pending",
            Task.due_date >= today_end,
            Task.due_date <= datetime(week_out.year, week_out.month, week_out.day),
        )
        .order_by(Task.due_date.asc())
        .all()
    )

    # ── TODAY'S APPOINTMENTS ─────────────────────────────────────
    todays_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_rep_id == rep_id,
            Appointment.appointment_date == today,
            Appointment.appointment_status.in_(["scheduled", "confirmed", "en_route", "arrived"]),
        )
        .order_by(Appointment.appointment_time.asc())
        .all()
    )

    def task_to_dict(task):
        lead = db.query(Lead).filter(Lead.id == task.lead_id).first()
        hours_overdue = None
        if task.status == "overdue" and task.due_date:
            hours_overdue = round((now - task.due_date).total_seconds() / 3600, 1)
        return {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type,
            "status": task.status,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "source_outcome": task.source_outcome,
            "lead_id": task.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
            "lead_address": lead.property_address if lead else None,
            "lead_phone": lead.phone if lead else None,
            "hours_overdue": hours_overdue,
            "notes": task.notes,
        }

    def appt_to_dict(appt):
        lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
        return {
            "id": appt.id,
            "lead_id": appt.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
            "address": appt.appointment_address or (lead.property_address if lead else None),
            "phone": lead.phone if lead else None,
            "time": str(appt.appointment_time) if appt.appointment_time else None,
            "status": appt.appointment_status,
        }

    # ── VIOLATIONS COUNT ─────────────────────────────────────────
    active_violations = (
        db.query(func.count(Violation.id))
        .filter(
            Violation.rep_id == rep_id,
            Violation.acknowledged == False,
        )
        .scalar() or 0
    )

    total_violations = (
        db.query(func.count(Violation.id))
        .filter(Violation.rep_id == rep_id)
        .scalar() or 0
    )

    return {
        "overdue": {
            "count": len(overdue_tasks),
            "tasks": [task_to_dict(t) for t in overdue_tasks],
        },
        "due_today": {
            "count": len(due_today_tasks),
            "tasks": [task_to_dict(t) for t in due_today_tasks],
        },
        "upcoming": {
            "count": len(upcoming_tasks),
            "tasks": [task_to_dict(t) for t in upcoming_tasks],
        },
        "appointments": {
            "count": len(todays_appointments),
            "items": [appt_to_dict(a) for a in todays_appointments],
        },
        "violations": {
            "active": active_violations,
            "total": total_violations,
        },
        "status": "red" if len(overdue_tasks) > 0 else ("amber" if len(due_today_tasks) > 0 else "green"),
    }


# ── ADMIN ENFORCEMENT OVERVIEW ─────────────────────────────────

def get_enforcement_overview(db: Session) -> dict:
    """
    Admin-level enforcement overview across all reps.

    Returns per-rep violation counts, overdue task counts,
    held rate, and close rate.
    """
    run_enforcement_check(db)

    reps = db.query(User).filter(User.role == "rep", User.is_active == True).all()

    rep_data = []
    for rep in reps:
        # Overdue tasks
        overdue_count = (
            db.query(func.count(Task.id))
            .filter(Task.assigned_to == rep.id, Task.status == "overdue")
            .scalar() or 0
        )

        # Pending tasks
        pending_count = (
            db.query(func.count(Task.id))
            .filter(Task.assigned_to == rep.id, Task.status == "pending")
            .scalar() or 0
        )

        # Active violations
        violation_count = (
            db.query(func.count(Violation.id))
            .filter(Violation.rep_id == rep.id, Violation.acknowledged == False)
            .scalar() or 0
        )

        # Total violations (all time)
        total_violations = (
            db.query(func.count(Violation.id))
            .filter(Violation.rep_id == rep.id)
            .scalar() or 0
        )

        # Held rate
        completed_appts = (
            db.query(Appointment)
            .filter(
                Appointment.assigned_rep_id == rep.id,
                Appointment.outcome.isnot(None),
            )
            .all()
        )
        held_outcomes = {"closed", "proposal_presented", "proposal_sent", "follow_up_required", "declined"}
        held = [a for a in completed_appts if a.outcome in held_outcomes]
        total_w_outcome = len(completed_appts)
        held_rate = round((len(held) / total_w_outcome * 100) if total_w_outcome > 0 else 0, 1)

        # Close rate (from held)
        closed = [a for a in completed_appts if a.outcome == "closed"]
        close_rate = round((len(closed) / len(held) * 100) if held else 0, 1)

        status = "red" if overdue_count > 0 else ("amber" if pending_count > 0 else "green")

        rep_data.append({
            "id": rep.id,
            "name": rep.full_name,
            "status": status,
            "overdue_tasks": overdue_count,
            "pending_tasks": pending_count,
            "active_violations": violation_count,
            "total_violations": total_violations,
            "held_rate": held_rate,
            "close_rate": close_rate,
            "total_appointments": total_w_outcome,
        })

    # Sort: worst performers first (most violations + overdue)
    rep_data.sort(key=lambda r: (r["active_violations"] + r["overdue_tasks"]), reverse=True)

    # Global stats
    total_overdue = sum(r["overdue_tasks"] for r in rep_data)
    total_violations = sum(r["active_violations"] for r in rep_data)
    reps_in_red = len([r for r in rep_data if r["status"] == "red"])

    return {
        "global": {
            "total_overdue_tasks": total_overdue,
            "total_active_violations": total_violations,
            "reps_in_red": reps_in_red,
            "total_reps": len(rep_data),
        },
        "reps": rep_data,
    }


# ── VIOLATION HELPERS ──────────────────────────────────────────

def create_violation(
    db: Session,
    rep_id: int,
    violation_type: str,
    description: str,
    severity: str = "warning",
    lead_id: int = None,
    task_id: int = None,
    appointment_id: int = None,
) -> Violation:
    """Create a violation record. Does NOT commit — caller must commit."""
    violation = Violation(
        rep_id=rep_id,
        violation_type=violation_type,
        severity=severity,
        lead_id=lead_id,
        task_id=task_id,
        appointment_id=appointment_id,
        description=description,
    )
    db.add(violation)
    return violation


def get_rep_violations(db: Session, rep_id: int, unacknowledged_only: bool = True) -> list:
    """Get violations for a specific rep."""
    query = db.query(Violation).filter(Violation.rep_id == rep_id)
    if unacknowledged_only:
        query = query.filter(Violation.acknowledged == False)
    return query.order_by(Violation.created_at.desc()).all()
