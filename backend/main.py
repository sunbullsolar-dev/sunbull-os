"""Sunbull OS - Main FastAPI Application"""
import os
import sys
from pathlib import Path

# Ensure backend directory is in Python path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Import database
from app.database import engine, Base, SessionLocal

# Import ALL models to register them with SQLAlchemy
from app.models import (
    User, Lead, Appointment, ConfirmationAttempt, AuditLog,
    LeadOwnershipHistory, FollowUp, Deal, Commission,
    InstallerProfile, AutomationRule, Notification,
    AccountabilityFlag, RehashEntry, BillAnalysis, WebsitePage,
    UserRole, LeadStatus, LeadSource, AppointmentStatus,
    DealStage, CommissionStatus,
)

# Import routers
from routes.auth import router as auth_router
from routes.leads import router as leads_router
from routes.appointments import router as appointments_router
from routes.confirmation import router as confirmation_router
from routes.deals import router as deals_router
from routes.admin import router as admin_router
from routes.rules import router as rules_router
from routes.dispatch import router as dispatch_router
from routes.solar import router as solar_router

# Create FastAPI app
app = FastAPI(
    title="Sunbull OS",
    description="Solar sales operations command center",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routers
app.include_router(auth_router)
app.include_router(leads_router)
app.include_router(appointments_router)
app.include_router(confirmation_router)
app.include_router(deals_router)
app.include_router(admin_router)
app.include_router(rules_router)
app.include_router(dispatch_router)
app.include_router(solar_router)


@app.on_event("startup")
def startup_event():
    """Create database tables and seed initial data."""
    Base.metadata.create_all(bind=engine)
    print("Database tables created.")

    db = SessionLocal()
    try:
        # Only seed if no admin exists
        admin_user = db.query(User).filter(User.email == "sunbullsolar@gmail.com").first()
        if admin_user:
            print("Seed data already exists.")
            return

        from app.auth import hash_password
        from datetime import datetime, timedelta, date, time
        import random
        import json

        # ---- USERS ----
        # Admin: Abdo Yaghi (owner)
        admin = User(
            email="sunbullsolar@gmail.com",
            hashed_password=hash_password("admin123"),
            full_name="Abdo Yaghi",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        db.flush()

        # Real Sunbull sales reps
        rep_data = [
            ("Nick Secor", "nicholassecor100@gmail.com"),
            ("Johannes Ayarian", "ohannes.ayarian@gmail.com"),
            ("Tyler Breskin", "breskinty@yahoo.com"),
            ("Baraa Alkassir", "baraa.alkassir@gmail.com"),
            ("Zack Diment", "zacharydiment26@gmail.com"),
            ("Ralph Munoz", "ralphhmun@gmail.com"),
            ("Angelo Mora", "jr.adasha99@gmail.com"),
            ("Eddie Walker", "thisiseddieemail@gmail.com"),
            ("Anna Aslanian", "aslanian_anna@yahoo.com"),
            ("Joshua Kirshner", "joshuabkirshner@gmail.com"),
            ("Mesrop Panosyan", "mesroppanosyano@gmail.com"),
            ("Benny Bruskin", "bensterb@gmail.com"),
            ("Rowan Nasser", "rowan.nasser96@gmail.com"),
            ("Juan Gudiel", "juangudiel90@icloud.com"),
            ("Nikka Gem Lumanac", "nikkageml@gmail.com"),
            ("Erik Estrada", "eoe8891@gmail.com"),
            ("Brian Fusi", "brianmfusi@gmail.com"),
            ("Alexander Dominguez", "alexdominguez_69@gmail.com"),
            ("Jordan Kahaner", "jordankahaner@gmail.com"),
            ("Ruben Bagdasarian", "rubenlida2000@gmail.com"),
            ("Itamar Azulay", "itamarazulay1@gmail.com"),
            ("Rome Yostin", "romeyostin@gmail.com"),
            ("Jane Millers", "janee.millers@gmail.com"),
            ("Hazel Parker", "hazel.parker.tact@gmail.com"),
            ("Vera Salem", "veralopezre@gmail.com"),
            ("Sarah Jacob", "sarahhjacob003@gmail.com"),
        ]

        all_reps = []
        for i, (name, email_addr) in enumerate(rep_data):
            rep = User(
                email=email_addr,
                hashed_password=hash_password("sunbull2026"),
                full_name=name,
                role="rep",
                close_rate=0.0,  # placeholder until real data loaded
                total_deals=0,
                territory=str(900 + i),
                is_active=True,
            )
            db.add(rep)
            all_reps.append(rep)
        db.flush()

        # Use first 3 reps for seed data references
        rep1, rep2, rep3 = all_reps[0], all_reps[1], all_reps[2]

        conf = User(
            email="confirm@sunbull.com",
            hashed_password=hash_password("sunbull2026"),
            full_name="Confirmation Team",
            role="confirmation",
            is_active=True,
        )
        db.add(conf)

        inst1 = User(
            email="installer1@sunbull.com",
            hashed_password=hash_password("sunbull2026"),
            full_name="Install Team A",
            role="installer",
            is_active=True,
        )
        inst2 = User(
            email="installer2@sunbull.com",
            hashed_password=hash_password("sunbull2026"),
            full_name="Install Team B",
            role="installer",
            is_active=True,
        )
        db.add_all([inst1, inst2])
        db.flush()  # Get IDs

        # ---- LEADS ----
        # (first_name, last_name, property_address, city, state, zip_code, phone, email,
        #  average_monthly_bill, estimated_annual_kwh, source_type, deal_status)
        leads_data = [
            ("Robert", "Garcia", "123 Oak St", "Phoenix", "AZ", "85001", "555-1001", "robert@email.com", 285.0, 14400, "door_to_door", "confirmed"),
            ("Emily", "Davis", "456 Pine Ave", "Los Angeles", "CA", "90001", "555-1002", "emily@email.com", 320.0, 16800, "call_center", "confirmed"),
            ("David", "Wilson", "789 Maple Dr", "Houston", "TX", "77001", "555-1003", "david@email.com", 195.0, 10800, "web", "new"),
            ("Sarah", "Anderson", "321 Elm St", "San Diego", "CA", "92101", "555-1004", "sarah@email.com", 410.0, 21600, "door_to_door", "appointed"),
            ("Michael", "Brown", "654 Cedar Ln", "Austin", "TX", "78701", "555-1005", "michael@email.com", 175.0, 9600, "web", "confirming"),
            ("Jessica", "Taylor", "987 Birch Rd", "Miami", "FL", "33101", "555-1006", "jessica@email.com", 350.0, 18000, "call_center", "new"),
            ("James", "Thomas", "111 Spruce Way", "Tampa", "FL", "33601", "555-1007", "james@email.com", 265.0, 13200, "door_to_door", "confirmed"),
            ("Amanda", "Jackson", "222 Willow Ct", "Sacramento", "CA", "95801", "555-1008", "amanda@email.com", 290.0, 15000, "web", "new"),
            ("Christopher", "White", "333 Ash Blvd", "Mesa", "AZ", "85201", "555-1009", "chris@email.com", 225.0, 12000, "call_center", "unconfirmed"),
            ("Ashley", "Harris", "444 Redwood St", "Orlando", "FL", "32801", "555-1010", "ashley@email.com", 380.0, 19800, "door_to_door", "appointed"),
            ("Daniel", "Martin", "555 Sequoia Dr", "Dallas", "TX", "75201", "555-1011", "daniel@email.com", 155.0, 8400, "web", "follow_up"),
            ("Nicole", "Lopez", "666 Cypress Ave", "San Antonio", "TX", "78201", "555-1012", "nicole@email.com", 340.0, 17400, "call_center", "confirmed"),
        ]

        from services.scoring import calculate_lead_score
        reps = [rep1, rep2, rep3]
        lead_objects = []
        now = datetime.utcnow()

        for i, (fn, ln, addr, city, st, zc, ph, em, bill, kwh, src, stat) in enumerate(leads_data):
            score = calculate_lead_score(
                average_monthly_bill=bill,
                city=city,
                state=st,
                confirmation_strength=1 if stat == "confirmed" else 0,
            )
            cost_per_kwh = round(bill * 12 / kwh, 4) if kwh else None

            lead = Lead(
                first_name=fn,
                last_name=ln,
                property_address=addr,
                city=city,
                state=st,
                zip_code=zc,
                phone=ph,
                email=em,
                average_monthly_bill=bill,
                estimated_annual_kwh=float(kwh),
                cost_per_kwh=cost_per_kwh,
                source_type=src,
                deal_status=stat,
                lead_quality_score=score,
                homeowner_status="owner",
                property_type="single_family",
                assigned_rep_id=reps[i % 3].id if stat != "new" else None,
                setter_id=reps[i % 3].id,
                created_at=now - timedelta(days=random.randint(1, 30)),
            )
            db.add(lead)
            lead_objects.append(lead)
        db.flush()

        # ---- APPOINTMENTS ----
        for i, lead in enumerate(lead_objects[:6]):
            if lead.assigned_rep_id:
                appt_date = (now + timedelta(days=random.randint(1, 7))).date()
                appt_time = time(hour=random.choice([9, 10, 11, 13, 14, 15, 16]))
                appt = Appointment(
                    lead_id=lead.id,
                    assigned_rep_id=lead.assigned_rep_id,
                    appointment_date=appt_date,
                    appointment_time=appt_time,
                    scheduled_duration_minutes=90,
                    appointment_status="scheduled" if i < 3 else "confirmed",
                    confirmation_status="pending" if i < 3 else "confirmed",
                    appointment_address=lead.property_address,
                    created_at=now - timedelta(days=random.randint(0, 5)),
                )
                db.add(appt)
        db.flush()

        # ---- DEALS (from closed leads) ----
        for lead in lead_objects[:2]:
            lead.deal_status = "closed_won"
            lead.closer_id = lead.assigned_rep_id
            deal_val = round(lead.average_monthly_bill * 12 * 20 * 0.6, 2)
            lead.deal_value = deal_val

            deal = Deal(
                lead_id=lead.id,
                rep_id=lead.assigned_rep_id,
                deal_value=deal_val,
                pipeline_stage="sold",
                installer_id=inst1.id,
                responsible_party="installer",
                sold_at=now - timedelta(days=random.randint(5, 15)),
                stage_entered_at=now - timedelta(days=random.randint(5, 15)),
            )
            db.add(deal)
            db.flush()

            commission_amount = round(deal_val * 0.14, 2)
            commission = Commission(
                deal_id=deal.id,
                rep_id=lead.assigned_rep_id,
                deal_value=deal_val,
                commission_rate=0.14,
                commission_amount=commission_amount,
                company_revenue=round(deal_val * 0.86, 2),
                status="pending",
            )
            db.add(commission)

            lead.commission_amount = commission_amount
        db.flush()

        # ---- INSTALLER PROFILES ----
        for inst in [inst1, inst2]:
            profile = InstallerProfile(
                user_id=inst.id,
                company_name=f"{inst.full_name}'s Solar Install",
                jobs_assigned=random.randint(10, 30),
                jobs_completed=random.randint(5, 20),
                avg_install_days=round(random.uniform(3, 8), 1),
                cancellation_rate=round(random.uniform(0.02, 0.15), 3),
                funding_delay_avg_days=round(random.uniform(5, 20), 1),
                performance_score=round(random.uniform(60, 95), 1),
                tier=random.choice(["bronze", "silver", "gold"]),
                is_active=True,
            )
            db.add(profile)

        # ---- AUTOMATION RULES ----
        rules = [
            AutomationRule(
                name="High-Value Lead → Top Rep",
                description="Assign leads with bills over $300 to the rep with the highest close rate",
                condition_field="average_monthly_bill",
                condition_operator="gt",
                condition_value="300",
                action_type="assign_rep",
                action_params=json.dumps({"strategy": "highest_close_rate"}),
                is_active=True,
                priority=10,
                created_by=admin.id,
            ),
            AutomationRule(
                name="Unconfirmed → Auto Reschedule",
                description="If lead is unconfirmed after 3 attempts, auto-reschedule",
                condition_field="deal_status",
                condition_operator="eq",
                condition_value="unconfirmed",
                action_type="change_status",
                action_params=json.dumps({"new_status": "reschedule"}),
                is_active=True,
                priority=20,
                created_by=admin.id,
            ),
            AutomationRule(
                name="Missed Update Alert",
                description="Flag rep if no status update within 30 minutes after appointment",
                condition_field="deal_status",
                condition_operator="eq",
                condition_value="appointed",
                action_type="send_alert",
                action_params=json.dumps({"alert_type": "missed_update", "notify_admin": True}),
                is_active=True,
                priority=5,
                created_by=admin.id,
            ),
            AutomationRule(
                name="Low Bill → Standard Queue",
                description="Leads under $150 go to standard dispatch queue",
                condition_field="average_monthly_bill",
                condition_operator="lt",
                condition_value="150",
                action_type="assign_rep",
                action_params=json.dumps({"strategy": "round_robin"}),
                is_active=False,
                priority=30,
                created_by=admin.id,
            ),
        ]
        db.add_all(rules)

        # ---- CONFIRMATION ATTEMPTS ----
        for lead in lead_objects[6:9]:
            for attempt_num in range(1, random.randint(2, 4)):
                ca = ConfirmationAttempt(
                    lead_id=lead.id,
                    agent_id=conf.id,
                    attempt_number=attempt_num,
                    outcome=random.choice(["answered", "no_answer", "voicemail"]),
                    called_at=now - timedelta(hours=random.randint(1, 48)),
                )
                db.add(ca)

        # ---- FOLLOW-UPS ----
        follow_up_lead = lead_objects[10]  # Daniel Martin - follow_up status
        follow_up_lead.follow_up_required = True
        follow_up_lead.next_follow_up_date = now + timedelta(days=2)
        follow_up = FollowUp(
            lead_id=follow_up_lead.id,
            assigned_rep_id=follow_up_lead.assigned_rep_id or rep1.id,
            reason="Customer wants more info on financing",
            scheduled_date=now + timedelta(days=2),
            status="pending",
            notes="Follow-up requested - customer interested but needs financing details",
        )
        db.add(follow_up)

        # ---- REHASH QUEUE ----
        rehash = RehashEntry(
            lead_id=lead_objects[10].id,
            original_rep_id=rep1.id,
            assigned_rep_id=rep2.id,
            reason="Follow-up requested - customer wants more info",
            callback_at=now + timedelta(days=2),
            attempts=1,
            status="pending",
        )
        db.add(rehash)

        # ---- NOTIFICATIONS ----
        notifs = [
            Notification(user_id=rep1.id, title="New Lead Assigned", message="Robert Garcia has been assigned to you", type="new_lead"),
            Notification(user_id=rep2.id, title="Appointment Confirmed", message="Emily Davis confirmed for tomorrow at 2 PM", type="success"),
            Notification(user_id=admin.id, title="Missed Update", message="Carlos Martinez has not updated status for appointment #3", type="warning"),
        ]
        db.add_all(notifs)

        # ---- AUDIT LOG ENTRIES ----
        for lead in lead_objects[:5]:
            entry = AuditLog(
                user_id=admin.id,
                action="create",
                entity_type="lead",
                entity_id=lead.id,
                details=f"Lead created: {lead.first_name} {lead.last_name}",
                created_at=lead.created_at,
            )
            db.add(entry)

        # ---- WEBSITE PAGES ----
        pages = [
            WebsitePage(
                slug="homepage",
                title="Go Solar with Sunbull",
                subtitle="Save money. Save the planet. Get your free solar estimate today.",
                body_content="Sunbull helps homeowners switch to solar with zero upfront cost. Our team handles everything from design to installation.",
                meta_description="Sunbull Solar - Free solar estimates for homeowners. Save up to 30% on your electric bill.",
                cta_text="Get My Free Estimate",
                cta_link="/savings-plan",
                sort_order=1,
                is_published=True,
            ),
            WebsitePage(
                slug="bill_upload",
                title="Upload Your Electric Bill",
                subtitle="We'll analyze your usage and show you exactly how much you can save.",
                body_content="Upload a photo or PDF of your latest electric bill. We'll extract your usage data and generate a personalized savings plan.",
                cta_text="Upload Bill",
                cta_link="/upload",
                sort_order=2,
                is_published=True,
            ),
            WebsitePage(
                slug="qualification_form",
                title="See If You Qualify",
                subtitle="Answer a few quick questions to get your personalized solar proposal.",
                body_content="Our qualification process is simple. We need your address, electric bill info, and a few details about your home.",
                cta_text="Check My Eligibility",
                cta_link="/qualify",
                sort_order=3,
                is_published=True,
            ),
            WebsitePage(
                slug="appointment_booking",
                title="Book Your Free Consultation",
                subtitle="Schedule a no-obligation appointment with a solar expert.",
                body_content="Choose a time that works for you. One of our certified solar consultants will visit your home and provide a detailed proposal.",
                cta_text="Book Now",
                cta_link="/book",
                sort_order=4,
                is_published=True,
            ),
            WebsitePage(
                slug="faq",
                title="Frequently Asked Questions",
                subtitle="Everything you need to know about going solar with Sunbull.",
                body_content="Q: How much does solar cost? A: With our financing options, most homeowners pay $0 upfront.\nQ: How long does installation take? A: Typically 1-3 days.\nQ: Will I still have an electric bill? A: You may have a small grid connection fee, but your solar system will offset most or all of your usage.",
                cta_text="Still Have Questions? Contact Us",
                cta_link="/contact",
                sort_order=5,
                is_published=True,
            ),
        ]
        db.add_all(pages)

        db.commit()
        print(f"Seeded: {len(leads_data)} leads, {len(rep_data)} reps, 2 installers, {len(rules)} rules, appointments, deals, commissions, website pages")

    except Exception as e:
        db.rollback()
        print(f"Seed error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


# ---- STATIC FILES & FRONTEND ----
frontend_path = Path(__file__).parent.parent / "frontend"
static_path = frontend_path / "static"
templates_path = frontend_path / "templates"

if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
async def root():
    """Serve the main frontend."""
    index_file = templates_path / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Sunbull OS API running. Frontend not found."}


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Sunbull OS"}


@app.get("/api")
def api_root():
    return {
        "service": "Sunbull OS",
        "version": "1.0.0",
        "endpoints": {
            "auth": "/api/auth/login",
            "leads": "/api/leads",
            "appointments": "/api/appointments",
            "confirmation": "/api/confirmation",
            "deals": "/api/deals",
            "admin": "/api/admin",
            "rules": "/api/rules",
            "dispatch": "/api/dispatch",
            "solar": "/api/solar",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
