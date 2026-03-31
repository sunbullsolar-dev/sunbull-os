"""
Sunbull OS — Phase 3 Engines
ESCALATION | PRIORITY | REVENUE | AUTO-REASSIGN | NOTIFICATIONS | LEADERBOARD

These engines turn the system into a revenue-driving machine.
"""
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from app.models import (
    Task, Lead, User, Appointment, Violation, Notification,
)


# =================================================================
# 1. ESCALATION ENGINE
# =================================================================

ESCALATION_THRESHOLDS = {
    "warning": 12,     # 12 hours overdue → warning
    "serious": 24,     # 24 hours → serious
    "critical": 48,    # 48 hours → critical, recommend reassign
}

HIGH_RISK_VIOLATION_THRESHOLD = 3  # 3+ active violations = HIGH RISK rep


def run_escalation_engine(db: Session) -> dict:
    """
    Escalation engine. Runs on dashboard load.

    1. Checks overdue tasks and escalates severity
    2. Flags high-risk reps (3+ violations)
    3. Creates escalation notifications for admin
    4. Identifies leads at risk of being lost

    Returns escalation summary.
    """
    now = datetime.utcnow()
    results = {
        "escalated_tasks": 0,
        "high_risk_reps": [],
        "leads_at_risk": [],
        "notifications_created": 0,
    }

    # ── 1. Escalate overdue tasks by severity ─────────────────
    overdue_tasks = (
        db.query(Task)
        .filter(Task.status == "overdue", Task.assigned_to.isnot(None))
        .all()
    )

    for task in overdue_tasks:
        if not task.due_date:
            continue
        hours_overdue = (now - task.due_date).total_seconds() / 3600

        # Upgrade violation severity if exists
        violation = (
            db.query(Violation)
            .filter(Violation.task_id == task.id, Violation.violation_type == "missed_task")
            .first()
        )
        if violation:
            new_severity = "warning"
            if hours_overdue >= ESCALATION_THRESHOLDS["critical"]:
                new_severity = "critical"
            elif hours_overdue >= ESCALATION_THRESHOLDS["serious"]:
                new_severity = "serious"
            elif hours_overdue >= ESCALATION_THRESHOLDS["warning"]:
                new_severity = "warning"

            if violation.severity != new_severity and new_severity in ("serious", "critical"):
                violation.severity = new_severity
                violation.description = f"ESCALATED ({new_severity}): Task overdue {int(hours_overdue)}h — {task.title}"
                results["escalated_tasks"] += 1

                # Create admin notification for critical escalations
                if new_severity == "critical":
                    _create_notification(
                        db, user_id=None,  # admin notification
                        title=f"CRITICAL: Task {int(hours_overdue)}h overdue",
                        message=f"Task '{task.title}' assigned to rep #{task.assigned_to} is {int(hours_overdue)}h overdue. Recommend reassignment.",
                        notif_type="escalation",
                        entity_type="task",
                        entity_id=task.id,
                    )
                    results["notifications_created"] += 1

    # ── 2. Flag high-risk reps ────────────────────────────────
    reps = db.query(User).filter(User.role == "rep", User.is_active == True).all()
    for rep in reps:
        active_violations = (
            db.query(func.count(Violation.id))
            .filter(Violation.rep_id == rep.id, Violation.acknowledged == False)
            .scalar() or 0
        )
        overdue_count = (
            db.query(func.count(Task.id))
            .filter(Task.assigned_to == rep.id, Task.status == "overdue")
            .scalar() or 0
        )

        if active_violations >= HIGH_RISK_VIOLATION_THRESHOLD:
            results["high_risk_reps"].append({
                "rep_id": rep.id,
                "name": rep.full_name,
                "active_violations": active_violations,
                "overdue_tasks": overdue_count,
                "risk_level": "critical" if active_violations >= 5 else "high",
            })

    # ── 3. Find leads at risk (inactive, overdue follow-ups) ──
    risk_leads = (
        db.query(Lead)
        .filter(
            Lead.is_archived == False,
            Lead.deal_status.notin_(["closed_won", "closed_lost"]),
            or_(
                # Follow-up overdue by 48+ hours
                and_(
                    Lead.follow_up_required == True,
                    Lead.next_follow_up_date < now - timedelta(hours=48),
                ),
                # No activity in 7+ days on active lead
                Lead.updated_at < now - timedelta(days=7),
            ),
        )
        .all()
    )

    for lead in risk_leads:
        deal_val = lead.deal_value if lead.deal_value and lead.deal_value > 0 else 0
        days_inactive = (now - lead.updated_at).days if lead.updated_at else 0
        fu_overdue_hours = None
        if lead.next_follow_up_date and lead.follow_up_required:
            fu_overdue_hours = int((now - lead.next_follow_up_date).total_seconds() / 3600)

        results["leads_at_risk"].append({
            "lead_id": lead.id,
            "name": f"{lead.first_name} {lead.last_name}",
            "deal_status": lead.deal_status,
            "deal_value": round(deal_val, 2),
            "assigned_rep_id": lead.assigned_rep_id,
            "days_inactive": days_inactive,
            "followup_overdue_hours": fu_overdue_hours,
            "reason": "overdue_followup" if fu_overdue_hours else "inactive",
        })

    results["leads_at_risk"].sort(key=lambda x: x["deal_value"], reverse=True)

    if results["escalated_tasks"] > 0:
        db.commit()

    return results


# =================================================================
# 2. PRIORITY ENGINE
# =================================================================

STAGE_WEIGHTS = {
    "closed_won": 0,       # Already won, not a priority
    "closed_lost": 0,      # Lost
    "new": 10,
    "confirmed": 20,
    "appointed": 40,
    "dispatched": 50,
    "follow_up": 60,       # High — needs action
    "rehash": 30,
    "no_show": 15,
}


def calculate_lead_priority(db: Session, lead: Lead) -> dict:
    """
    Calculate priority score for a single lead.

    Factors:
    - Deal value (higher = more priority)
    - Stage weight (proposal > new)
    - Time since last action (older = more urgent)
    - Follow-up urgency (overdue = critical)
    - Has open tasks (tasks pending = needs action)
    """
    now = datetime.utcnow()

    # Deal value score (0-30 points)
    deal_val = lead.deal_value if lead.deal_value and lead.deal_value > 0 else 0
    value_score = min(30, deal_val / 1000)  # Cap at 30

    # Stage weight (0-60 points)
    stage_score = STAGE_WEIGHTS.get(lead.deal_status, 10)

    # Time urgency (0-40 points) — more points for older inactive leads
    days_since_update = (now - lead.updated_at).days if lead.updated_at else 30
    time_score = min(40, days_since_update * 4)

    # Follow-up urgency (0-50 points)
    fu_score = 0
    if lead.follow_up_required and lead.next_follow_up_date:
        hours_until = (lead.next_follow_up_date - now).total_seconds() / 3600
        if hours_until < 0:  # Overdue
            fu_score = min(50, abs(hours_until) * 2)
        elif hours_until < 24:  # Due today
            fu_score = 30
        elif hours_until < 48:  # Due tomorrow
            fu_score = 15

    # Open tasks bonus
    open_tasks = (
        db.query(func.count(Task.id))
        .filter(Task.lead_id == lead.id, Task.status.in_(["pending", "overdue"]))
        .scalar() or 0
    )
    task_score = min(20, open_tasks * 10)

    total = round(value_score + stage_score + time_score + fu_score + task_score, 1)

    return {
        "lead_id": lead.id,
        "name": f"{lead.first_name} {lead.last_name}",
        "deal_status": lead.deal_status,
        "deal_value": round(deal_val, 2),
        "priority_score": total,
        "breakdown": {
            "value": round(value_score, 1),
            "stage": stage_score,
            "time_urgency": round(time_score, 1),
            "followup_urgency": round(fu_score, 1),
            "task_urgency": task_score,
        },
        "assigned_rep_id": lead.assigned_rep_id,
        "phone": lead.phone,
        "city": lead.city,
        "next_follow_up": str(lead.next_follow_up_date) if lead.next_follow_up_date else None,
        "days_since_update": days_since_update,
        "open_tasks": open_tasks,
    }


def get_top_leads_to_close(db: Session, limit: int = 10) -> list:
    """Get top leads ranked by priority score."""
    active_leads = (
        db.query(Lead)
        .filter(
            Lead.is_archived == False,
            Lead.deal_status.notin_(["closed_won", "closed_lost"]),
        )
        .all()
    )

    scored = [calculate_lead_priority(db, lead) for lead in active_leads]
    scored.sort(key=lambda x: x["priority_score"], reverse=True)
    return scored[:limit]


def get_rep_top_leads(db: Session, rep_id: int, limit: int = 5) -> list:
    """Get top priority leads for a specific rep."""
    active_leads = (
        db.query(Lead)
        .filter(
            Lead.is_archived == False,
            Lead.assigned_rep_id == rep_id,
            Lead.deal_status.notin_(["closed_won", "closed_lost"]),
        )
        .all()
    )

    scored = [calculate_lead_priority(db, lead) for lead in active_leads]
    scored.sort(key=lambda x: x["priority_score"], reverse=True)
    return scored[:limit]


# =================================================================
# 3. REVENUE VISIBILITY
# =================================================================

def get_revenue_dashboard(db: Session) -> dict:
    """
    Revenue visibility for admin.
    Shows where money is at every stage.
    """
    now = datetime.utcnow()

    active_leads = (
        db.query(Lead)
        .filter(Lead.is_archived == False, Lead.deal_status.notin_(["closed_won", "closed_lost"]))
        .all()
    )

    # Revenue by category
    revenue_open = 0        # All active deals
    revenue_followup = 0    # In follow-up stage
    revenue_overdue = 0     # Leads with overdue tasks/follow-ups
    revenue_proposal = 0    # Proposal sent/presented
    revenue_new = 0         # New leads

    for lead in active_leads:
        # Only count real contract values — no estimated formulas
        val = lead.deal_value if lead.deal_value and lead.deal_value > 0 else 0
        revenue_open += val

        if lead.deal_status == "follow_up" or lead.follow_up_required:
            revenue_followup += val

        if lead.deal_status in ("new", "confirmed"):
            revenue_new += val

        # Check for overdue follow-ups or tasks
        has_overdue = False
        if lead.follow_up_required and lead.next_follow_up_date and lead.next_follow_up_date < now:
            has_overdue = True
        overdue_tasks = (
            db.query(func.count(Task.id))
            .filter(Task.lead_id == lead.id, Task.status == "overdue")
            .scalar() or 0
        )
        if overdue_tasks > 0:
            has_overdue = True
        if has_overdue:
            revenue_overdue += val

    # Closed revenue
    closed_leads = (
        db.query(Lead)
        .filter(Lead.deal_status == "closed_won")
        .all()
    )
    revenue_closed = sum(l.deal_value or 0 for l in closed_leads)

    # Revenue by proposal stage
    proposal_leads = (
        db.query(Lead)
        .join(Appointment, Appointment.lead_id == Lead.id)
        .filter(
            Lead.is_archived == False,
            Appointment.outcome.in_(["proposal_presented", "proposal_sent"]),
            Lead.deal_status.notin_(["closed_won", "closed_lost"]),
        )
        .distinct()
        .all()
    )
    for lead in proposal_leads:
        val = lead.deal_value if lead.deal_value and lead.deal_value > 0 else 0
        revenue_proposal += val

    return {
        "revenue_open": round(revenue_open, 2),
        "revenue_at_risk": round(revenue_overdue, 2),
        "revenue_in_followups": round(revenue_followup, 2),
        "revenue_in_proposals": round(revenue_proposal, 2),
        "revenue_new_leads": round(revenue_new, 2),
        "revenue_closed": round(revenue_closed, 2),
        "total_active_leads": len(active_leads),
        "leads_at_risk_count": sum(1 for l in active_leads if l.follow_up_required and l.next_follow_up_date and l.next_follow_up_date < now),
    }


# =================================================================
# 4. AUTO-REASSIGNMENT ENGINE
# =================================================================

def get_reassignment_suggestions(db: Session) -> list:
    """
    Find leads that should be reassigned.

    Triggers:
    - Follow-up overdue by 72+ hours
    - No activity in 10+ days
    - Rep has 3+ violations on this lead
    """
    now = datetime.utcnow()
    suggestions = []

    leads = (
        db.query(Lead)
        .filter(
            Lead.is_archived == False,
            Lead.assigned_rep_id.isnot(None),
            Lead.deal_status.notin_(["closed_won", "closed_lost"]),
        )
        .all()
    )

    for lead in leads:
        reason = None
        urgency = "medium"

        # Follow-up overdue 72+ hours
        if lead.follow_up_required and lead.next_follow_up_date:
            hours_overdue = (now - lead.next_follow_up_date).total_seconds() / 3600
            if hours_overdue > 72:
                reason = f"Follow-up {int(hours_overdue)}h overdue"
                urgency = "critical" if hours_overdue > 120 else "high"

        # No activity in 10+ days
        if not reason and lead.updated_at:
            days_inactive = (now - lead.updated_at).days
            if days_inactive >= 10:
                reason = f"No activity for {days_inactive} days"
                urgency = "high" if days_inactive >= 14 else "medium"

        # Rep has violations on this lead
        if not reason:
            lead_violations = (
                db.query(func.count(Violation.id))
                .filter(Violation.lead_id == lead.id, Violation.acknowledged == False)
                .scalar() or 0
            )
            if lead_violations >= 2:
                reason = f"{lead_violations} unresolved violations on this lead"
                urgency = "high"

        if reason:
            rep = db.query(User).filter(User.id == lead.assigned_rep_id).first()
            deal_val = lead.deal_value if lead.deal_value and lead.deal_value > 0 else 0
            suggestions.append({
                "lead_id": lead.id,
                "lead_name": f"{lead.first_name} {lead.last_name}",
                "deal_value": round(deal_val, 2),
                "current_rep_id": lead.assigned_rep_id,
                "current_rep_name": rep.full_name if rep else "Unknown",
                "reason": reason,
                "urgency": urgency,
                "deal_status": lead.deal_status,
            })

    suggestions.sort(key=lambda x: {"critical": 0, "high": 1, "medium": 2}.get(x["urgency"], 3))
    return suggestions


# =================================================================
# 5. NOTIFICATION ENGINE
# =================================================================

def _create_notification(
    db: Session,
    user_id: Optional[int],
    title: str,
    message: str,
    notif_type: str = "info",
    entity_type: str = None,
    entity_id: int = None,
):
    """Create a notification. If user_id is None, notify all admins."""
    if user_id is None:
        admins = db.query(User).filter(User.role == "admin").all()
        for admin in admins:
            n = Notification(
                user_id=admin.id,
                title=title,
                message=message,
                type=notif_type,
                entity_type=entity_type,
                entity_id=entity_id,
            )
            db.add(n)
    else:
        n = Notification(
            user_id=user_id,
            title=title,
            message=message,
            type=notif_type,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.add(n)
    db.flush()


def get_user_notifications(db: Session, user_id: int, unread_only: bool = True, limit: int = 20) -> list:
    """Get notifications for a user."""
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    notifs = query.order_by(Notification.created_at.desc()).limit(limit).all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "type": n.type,
            "is_read": n.is_read,
            "entity_type": n.entity_type,
            "entity_id": n.entity_id,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifs
    ]


def mark_notification_read(db: Session, notification_id: int, user_id: int):
    """Mark a notification as read."""
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user_id).first()
    if n:
        n.is_read = True
        db.commit()
    return n


def mark_all_notifications_read(db: Session, user_id: int):
    """Mark all notifications as read for a user."""
    db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()


def create_overdue_notifications(db: Session):
    """Create notifications for overdue tasks (once per task per day)."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    overdue_tasks = (
        db.query(Task)
        .filter(Task.status == "overdue", Task.assigned_to.isnot(None))
        .all()
    )

    created = 0
    for task in overdue_tasks:
        # Check if notification already sent today
        existing = (
            db.query(Notification)
            .filter(
                Notification.user_id == task.assigned_to,
                Notification.entity_type == "task",
                Notification.entity_id == task.id,
                Notification.created_at >= today_start,
            )
            .first()
        )
        if not existing:
            hours = int((now - task.due_date).total_seconds() / 3600) if task.due_date else 0
            _create_notification(
                db,
                user_id=task.assigned_to,
                title=f"OVERDUE: {task.title}",
                message=f"This task is {hours}h overdue. Complete it NOW.",
                notif_type="overdue",
                entity_type="task",
                entity_id=task.id,
            )
            created += 1

    if created > 0:
        db.commit()
    return created


# =================================================================
# 6. REP LEADERBOARD
# =================================================================

def get_rep_leaderboard(db: Session) -> dict:
    """
    Build the rep leaderboard.
    Ranks reps by composite daily score.
    Shows top performers and flags low performers.
    """
    from services.enforcement import calculate_rep_performance

    reps = db.query(User).filter(User.role == "rep", User.is_active == True).all()
    board = []

    for rep in reps:
        perf = calculate_rep_performance(db, rep.id)

        # Daily score calculation
        # Positive: close rate, held rate, revenue
        # Negative: violations, overdue tasks
        daily_score = round(
            perf["close_rate"] * 3
            + perf["held_rate"] * 2
            + min(50, perf["total_revenue"] / 500)  # Revenue bonus capped at 50
            - perf["overdue_tasks"] * 20
            - perf["active_violations"] * 25,
            1
        )

        # Determine rank tier
        if daily_score >= 300:
            tier = "elite"
        elif daily_score >= 200:
            tier = "strong"
        elif daily_score >= 100:
            tier = "average"
        elif daily_score >= 0:
            tier = "below_average"
        else:
            tier = "underperforming"

        board.append({
            "rep_id": rep.id,
            "name": rep.full_name,
            "email": rep.email,
            "daily_score": daily_score,
            "tier": tier,
            "close_rate": perf["close_rate"],
            "held_rate": perf["held_rate"],
            "closed_count": perf["closed_count"],
            "total_revenue": perf["total_revenue"],
            "active_leads": perf["active_leads"],
            "overdue_tasks": perf["overdue_tasks"],
            "active_violations": perf["active_violations"],
            "open_tasks": perf["open_tasks"],
        })

    board.sort(key=lambda x: x["daily_score"], reverse=True)

    # Assign ranks
    for i, rep in enumerate(board):
        rep["rank"] = i + 1

    top_performers = [r for r in board if r["tier"] in ("elite", "strong")]
    low_performers = [r for r in board if r["tier"] in ("below_average", "underperforming")]

    return {
        "leaderboard": board,
        "top_performers": top_performers,
        "low_performers": low_performers,
        "total_reps": len(board),
    }


# =================================================================
# 7. SMART DISPATCH LEVEL 2
# =================================================================

def smart_recommend_rep(db: Session, lead_id: int) -> list:
    """
    Enhanced rep recommendation including:
    - Priority score of the lead
    - Deal value consideration
    - Rep performance + workload
    - Auto-assign flag for low-risk leads
    """
    from services.enforcement import calculate_rep_performance

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        return []

    lead_priority = calculate_lead_priority(db, lead)
    deal_val = lead_priority["deal_value"]
    is_high_value = deal_val > 15000
    is_low_risk = lead.deal_status in ("new", "confirmed") and deal_val < 5000

    reps = db.query(User).filter(User.role == "rep", User.is_active == True).all()
    scored = []

    for rep in reps:
        perf = calculate_rep_performance(db, rep.id)

        # Base performance score
        workload_score = max(0, 100 - perf["active_leads"] * 10 - perf["open_tasks"] * 15)
        violation_penalty = perf["active_violations"] * 25

        # For high-value leads, weight close rate more heavily
        if is_high_value:
            score = perf["close_rate"] * 5 + perf["held_rate"] * 2 + workload_score - violation_penalty
        else:
            score = perf["close_rate"] * 3 + perf["held_rate"] * 2 + workload_score * 2 - violation_penalty

        # Bonus for reps with low overdue tasks
        if perf["overdue_tasks"] == 0:
            score += 20

        scored.append({
            "rep_id": rep.id,
            "rep_name": rep.full_name,
            "email": rep.email,
            "score": round(score, 1),
            "close_rate": perf["close_rate"],
            "held_rate": perf["held_rate"],
            "active_leads": perf["active_leads"],
            "open_tasks": perf["open_tasks"],
            "overdue_tasks": perf["overdue_tasks"],
            "violations": perf["active_violations"],
            "auto_assign_eligible": is_low_risk and perf["active_violations"] == 0 and perf["overdue_tasks"] == 0,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    return {
        "lead_priority": lead_priority["priority_score"],
        "deal_value": deal_val,
        "is_high_value": is_high_value,
        "is_low_risk": is_low_risk,
        "recommendations": scored[:5],
    }
