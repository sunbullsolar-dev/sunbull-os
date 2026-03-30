"""
Sunbull OS — Next Action Engine (THE BRAIN)

Central function: process_outcome(db, appointment_id, outcome, notes, ...)

This is the ENFORCEMENT layer. Every appointment completion flows through here.
It validates, determines held/not-held, updates statuses, and CREATES NEXT ACTIONS.

RULES (LAW):
- Cannot complete without ARRIVED status
- Cannot mark SOLD unless HELD
- Notes always required
- Follow-up date required for follow-up outcomes
- Every outcome creates the correct next task automatically
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from app.models import (
    Appointment, Lead, User, Task, Project, FollowUp, RehashEntry,
    Action, AuditLog, LeadOwnershipHistory,
)


# ── Outcome Classification ──────────────────────────────────────
HELD_OUTCOMES = {
    "closed", "proposal_presented", "proposal_sent",
    "follow_up_required", "declined",
}
NOT_HELD_OUTCOMES = {
    "no_show", "not_home", "canceled", "reschedule_requested",
}
ALL_OUTCOMES = HELD_OUTCOMES | NOT_HELD_OUTCOMES

REQUIRES_FOLLOW_UP = {
    "proposal_presented", "proposal_sent", "follow_up_required",
    "not_home", "reschedule_requested",
}


class OutcomeError(Exception):
    """Raised when outcome processing fails validation."""
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def process_outcome(
    db: Session,
    appointment_id: int,
    outcome: str,
    notes: str,
    current_user: User,
    follow_up_date: Optional[str] = None,
    follow_up_note: Optional[str] = None,
) -> dict:
    """
    THE BRAIN — processes an appointment outcome and creates all next actions.

    Returns dict with result details.
    Raises OutcomeError on validation failure.
    """

    # ── 1. LOAD & VALIDATE ───────────────────────────────────────
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise OutcomeError("Appointment not found", 404)

    if current_user.role != "admin" and current_user.id != appointment.assigned_rep_id:
        raise OutcomeError("Not your appointment", 403)

    # Block double-complete — if outcome already set, reject
    if appointment.outcome is not None:
        raise OutcomeError(
            f"Appointment already completed with outcome '{appointment.outcome}'. Cannot complete again.",
            400,
        )

    if appointment.appointment_status not in ("arrived", "en_route"):
        raise OutcomeError(
            f"Cannot complete from '{appointment.appointment_status}'. Must be arrived or en_route.",
            400,
        )

    if outcome not in ALL_OUTCOMES:
        raise OutcomeError(
            f"Invalid outcome '{outcome}'. Must be one of: {', '.join(sorted(ALL_OUTCOMES))}",
            400,
        )

    if not notes or not notes.strip():
        raise OutcomeError("Notes are required", 400)

    # Cannot mark SOLD unless HELD
    if outcome == "closed" and outcome not in HELD_OUTCOMES:
        raise OutcomeError("Cannot mark as closed — must be a held outcome", 400)

    # Follow-up date enforcement
    fu_date = None
    if outcome in REQUIRES_FOLLOW_UP:
        if not follow_up_date:
            raise OutcomeError(
                f"follow_up_date is required for '{outcome}' outcome", 400
            )
        if not follow_up_note or not follow_up_note.strip():
            raise OutcomeError(
                f"follow_up_note is required for '{outcome}' outcome", 400
            )
        try:
            fu_date = datetime.strptime(follow_up_date, "%Y-%m-%d")
        except ValueError:
            raise OutcomeError("follow_up_date must be YYYY-MM-DD format", 400)
        if fu_date.date() < datetime.utcnow().date():
            raise OutcomeError("follow_up_date must be today or in the future", 400)

    # ── 2. DETERMINE HELD/NOT-HELD ───────────────────────────────
    is_held = outcome in HELD_OUTCOMES

    # ── 3. UPDATE APPOINTMENT ────────────────────────────────────
    appointment.actual_end_time = datetime.utcnow()
    appointment.rep_checked_out_at = datetime.utcnow()
    appointment.outcome = outcome
    appointment.notes = notes

    if outcome == "canceled":
        appointment.appointment_status = "cancelled"
    elif outcome == "no_show":
        appointment.appointment_status = "no_show"
    elif outcome == "not_home":
        appointment.appointment_status = "no_show"
        appointment.reschedule_count = (appointment.reschedule_count or 0) + 1
    elif outcome == "reschedule_requested":
        appointment.appointment_status = "rescheduled"
        appointment.reschedule_count = (appointment.reschedule_count or 0) + 1
    else:
        appointment.appointment_status = "completed"

    # ── 4. UPDATE LEAD ───────────────────────────────────────────
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if not lead:
        raise OutcomeError(f"Lead #{appointment.lead_id} not found for this appointment", 404)
    tasks_created = []
    project_created = None

    if lead:
        lead.is_held = is_held
        lead.last_outcome = outcome
        lead.runner_id = appointment.assigned_rep_id  # Track who ran

        # ── OUTCOME → LEAD STATUS MAPPING ────────────────────────
        if outcome == "closed":
            lead.deal_status = "closed_won"
            lead.follow_up_required = False
            lead.closer_id = current_user.id  # Track who closed

        elif outcome in ("proposal_presented", "proposal_sent"):
            lead.deal_status = "proposal_sent"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = follow_up_note

        elif outcome == "follow_up_required":
            lead.deal_status = "follow_up"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = follow_up_note

        elif outcome == "declined":
            lead.deal_status = "declined"
            lead.follow_up_required = False

        elif outcome == "no_show":
            lead.deal_status = "no_show"
            lead.follow_up_required = False
            lead.lead_quality_score = max(0, (lead.lead_quality_score or 50) - 20)

        elif outcome == "not_home":
            lead.deal_status = "reschedule"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = follow_up_note

        elif outcome == "canceled":
            lead.deal_status = "canceled"
            lead.follow_up_required = False

        elif outcome == "reschedule_requested":
            lead.deal_status = "reschedule"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = follow_up_note

        db.flush()

        # ── 5. CREATE NEXT ACTIONS (THE MOST IMPORTANT PART) ─────

        # === CLOSED (SIGNED) → Create PROJECT ===
        if outcome == "closed":
            existing_project = db.query(Project).filter(Project.lead_id == lead.id).first()
            if existing_project:
                project_created = existing_project.id
            else:
                project = Project(
                    lead_id=lead.id,
                    appointment_id=appointment.id,
                    closer_id=current_user.id,
                    system_size_kw=lead.system_size_kw,
                    panel_count=lead.panel_count,
                    deal_value=lead.deal_value,
                    install_status="pending_survey",
                    funding_status="pending",
                    payment_status="pending",
                    sold_date=datetime.utcnow(),
                )
                db.add(project)
                db.flush()
                project_created = project.id

                # Create admin task: schedule site survey
                survey_task = Task(
                    lead_id=lead.id,
                    appointment_id=appointment.id,
                    assigned_to=current_user.id,  # Initially assigned to closer
                    created_by=current_user.id,
                    task_type="install_coordination",
                    title=f"Schedule site survey — {lead.first_name} {lead.last_name}",
                    description=f"Deal closed. Schedule site survey for {lead.property_address}.",
                    due_date=datetime.utcnow() + timedelta(days=3),
                    status="pending",
                    source_outcome="closed",
                    notes=notes,
                )
                db.add(survey_task)
                tasks_created.append("install_coordination: Schedule site survey")

        # === PROPOSAL PRESENTED → Follow-up in 24h ===
        elif outcome == "proposal_presented":
            task = Task(
                lead_id=lead.id,
                appointment_id=appointment.id,
                assigned_to=appointment.assigned_rep_id,
                created_by=current_user.id,
                task_type="follow_up",
                title=f"Follow up on proposal — {lead.first_name} {lead.last_name}",
                description=f"Proposal presented in person. Follow up within 24 hours.",
                due_date=fu_date if fu_date else datetime.utcnow() + timedelta(hours=24),
                status="pending",
                source_outcome="proposal_presented",
                notes=follow_up_note,
            )
            db.add(task)
            tasks_created.append("follow_up: Proposal presented follow-up (24h)")

        # === PROPOSAL SENT → Follow-up in 48h ===
        elif outcome == "proposal_sent":
            task = Task(
                lead_id=lead.id,
                appointment_id=appointment.id,
                assigned_to=appointment.assigned_rep_id,
                created_by=current_user.id,
                task_type="follow_up",
                title=f"Follow up on proposal sent — {lead.first_name} {lead.last_name}",
                description=f"Proposal sent after visit. Follow up within 48 hours.",
                due_date=fu_date if fu_date else datetime.utcnow() + timedelta(hours=48),
                status="pending",
                source_outcome="proposal_sent",
                notes=follow_up_note,
            )
            db.add(task)
            tasks_created.append("follow_up: Proposal sent follow-up (48h)")

        # === FOLLOW-UP NEEDED → Custom follow-up date ===
        elif outcome == "follow_up_required":
            task = Task(
                lead_id=lead.id,
                appointment_id=appointment.id,
                assigned_to=appointment.assigned_rep_id,
                created_by=current_user.id,
                task_type="follow_up",
                title=f"Follow up — {lead.first_name} {lead.last_name}",
                description=f"Conversation held, no proposal yet. Follow up required.",
                due_date=fu_date,
                status="pending",
                source_outcome="follow_up_required",
                notes=follow_up_note,
            )
            db.add(task)
            tasks_created.append("follow_up: Follow-up needed")

        # === DECLINED → Send to rehash queue ===
        elif outcome == "declined":
            task = Task(
                lead_id=lead.id,
                appointment_id=appointment.id,
                assigned_to=appointment.assigned_rep_id,
                created_by=current_user.id,
                task_type="rehash",
                title=f"Rehash — {lead.first_name} {lead.last_name} (declined)",
                description=f"Homeowner declined. Add to rehash queue for re-engagement.",
                due_date=datetime.utcnow() + timedelta(days=14),
                status="pending",
                source_outcome="declined",
                notes=notes,
            )
            db.add(task)
            tasks_created.append("rehash: Declined — rehash in 14 days")

            # Also create RehashEntry for queue tracking
            rehash = RehashEntry(
                lead_id=lead.id,
                original_rep_id=appointment.assigned_rep_id,
                reason=f"Declined after held appointment #{appointment.id}",
                status="pending",
            )
            db.add(rehash)

        # === NOT HOME → Reschedule task ===
        elif outcome == "not_home":
            task = Task(
                lead_id=lead.id,
                appointment_id=appointment.id,
                assigned_to=appointment.assigned_rep_id,
                created_by=current_user.id,
                task_type="reschedule",
                title=f"Reschedule — {lead.first_name} {lead.last_name} (not home)",
                description=f"Decision maker not home. Must reschedule.",
                due_date=fu_date if fu_date else datetime.utcnow() + timedelta(days=1),
                status="pending",
                source_outcome="not_home",
                notes=follow_up_note,
            )
            db.add(task)
            tasks_created.append("reschedule: Not home — reschedule")

        # === RESCHEDULE REQUESTED → Reschedule task ===
        elif outcome == "reschedule_requested":
            task = Task(
                lead_id=lead.id,
                appointment_id=appointment.id,
                assigned_to=appointment.assigned_rep_id,
                created_by=current_user.id,
                task_type="reschedule",
                title=f"Reschedule — {lead.first_name} {lead.last_name}",
                description=f"Homeowner requested reschedule.",
                due_date=fu_date if fu_date else datetime.utcnow() + timedelta(days=1),
                status="pending",
                source_outcome="reschedule_requested",
                notes=follow_up_note,
            )
            db.add(task)
            tasks_created.append("reschedule: Reschedule requested")

        # === NO SHOW → Mark low quality + rehash ===
        elif outcome == "no_show":
            task = Task(
                lead_id=lead.id,
                appointment_id=appointment.id,
                assigned_to=appointment.assigned_rep_id,
                created_by=current_user.id,
                task_type="rehash",
                title=f"Rehash — {lead.first_name} {lead.last_name} (no show)",
                description=f"No-show. Lead quality reduced. Add to rehash queue.",
                due_date=datetime.utcnow() + timedelta(days=7),
                status="pending",
                source_outcome="no_show",
                notes=notes,
            )
            db.add(task)
            tasks_created.append("rehash: No show — rehash in 7 days")

            # Also create RehashEntry
            rehash = RehashEntry(
                lead_id=lead.id,
                original_rep_id=appointment.assigned_rep_id,
                reason=f"No-show on appointment #{appointment.id}",
                status="pending",
            )
            db.add(rehash)

        # === CANCELED → No auto-task, just log ===
        elif outcome == "canceled":
            # No task created — appointment was canceled
            pass

        # Also create legacy FollowUp records for backward compat
        if outcome in REQUIRES_FOLLOW_UP:
            reason_map = {
                "proposal_presented": "Follow up on proposal presented in person",
                "proposal_sent": "Follow up on proposal sent after visit",
                "follow_up_required": "Follow up — conversation but no proposal yet",
                "not_home": "Reschedule — decision maker not home",
                "reschedule_requested": "Homeowner requested reschedule",
            }
            follow_up = FollowUp(
                lead_id=lead.id,
                assigned_rep_id=appointment.assigned_rep_id,
                reason=reason_map.get(outcome, f"Follow up from appointment #{appointment.id}"),
                scheduled_date=fu_date,
                status="pending",
                notes=follow_up_note,
            )
            db.add(follow_up)

    # ── 6. AUDIT + ACTION LOGGING ────────────────────────────────
    held_label = "HELD" if is_held else "NOT HELD"

    audit = AuditLog(
        user_id=current_user.id,
        entity_type="appointment",
        entity_id=appointment.id,
        action="complete",
        new_value=outcome,
        details=f"{held_label}: {outcome}. Tasks: {', '.join(tasks_created) if tasks_created else 'none'}",
    )
    db.add(audit)

    action = Action(
        lead_id=appointment.lead_id,
        rep_id=current_user.id,
        appointment_id=appointment.id,
        action_type=outcome,
        note=f"[{held_label}] {notes}",
    )
    db.add(action)

    if tasks_created:
        task_action = Action(
            lead_id=appointment.lead_id,
            rep_id=current_user.id,
            appointment_id=appointment.id,
            action_type="tasks_created",
            note=f"Auto-created: {', '.join(tasks_created)}",
        )
        db.add(task_action)

    # ── 7. OWNERSHIP HISTORY ─────────────────────────────────────
    if lead:
        ownership = LeadOwnershipHistory(
            lead_id=lead.id,
            user_id=current_user.id,
            role_in_lead="runner",
            notes=f"Ran appointment #{appointment.id}, outcome: {outcome}",
        )
        db.add(ownership)

        if outcome == "closed":
            closer_ownership = LeadOwnershipHistory(
                lead_id=lead.id,
                user_id=current_user.id,
                role_in_lead="closer",
                notes=f"Closed deal from appointment #{appointment.id}",
            )
            db.add(closer_ownership)

    # Commit everything
    db.commit()
    db.refresh(appointment)

    return {
        "id": appointment.id,
        "appointment_status": appointment.appointment_status,
        "outcome": outcome,
        "is_held": is_held,
        "lead_status": lead.deal_status if lead else None,
        "tasks_created": tasks_created,
        "project_id": project_created,
        "follow_up_date": follow_up_date,
        "follow_up_note": follow_up_note,
    }


def check_open_tasks(db: Session, lead_id: int) -> list:
    """Check for open (pending/overdue) tasks on a lead."""
    return (
        db.query(Task)
        .filter(
            Task.lead_id == lead_id,
            Task.status.in_(["pending", "overdue"]),
        )
        .all()
    )


def mark_overdue_tasks(db: Session) -> int:
    """Mark all past-due pending tasks as overdue. Returns count updated."""
    now = datetime.utcnow()
    overdue_tasks = (
        db.query(Task)
        .filter(
            Task.status == "pending",
            Task.due_date < now,
        )
        .all()
    )
    count = 0
    for task in overdue_tasks:
        task.status = "overdue"
        count += 1
    if count > 0:
        db.commit()
    return count
