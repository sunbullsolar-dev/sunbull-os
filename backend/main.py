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
    Action, Task, Project, Violation, Invite, Comment, FileUpload,
    UserRole, LeadStatus, LeadSource, AppointmentStatus,
    DealStage, CommissionStatus, SolarEstimate,
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
from routes.solar_engine import router as solar_engine_router
from routes.tasks import router as tasks_router
from routes.comments import router as comments_router
from routes.uploads import router as uploads_router
from routes.projects import router as projects_router

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
app.include_router(solar_engine_router)
app.include_router(tasks_router)
app.include_router(comments_router)
app.include_router(uploads_router)
app.include_router(projects_router)



@app.get("/api/config/public")
def get_public_config():
    """Return non-secret config values for frontend (e.g. Google Maps API key)."""
    return {
        "google_maps_api_key": os.environ.get("GOOGLE_MAPS_API_KEY", ""),
    }


@app.on_event("startup")
def startup_event():
    """Create database tables and seed initial data."""
    Base.metadata.create_all(bind=engine)
    print("Database tables created.")

    # Add new columns to existing tables (already handled in seed logic below)
    print("Database initialization complete.")

    # Universal auto-migration: compare ALL model columns to DB and add missing ones
    try:
        from sqlalchemy import inspect, text as _sql_text, String, Integer, Float, Boolean, Text, DateTime, Date, Time, JSON
        inspector = inspect(engine)

        def sa_type_to_sql(col):
            t = type(col.type)
            if t in (String,):
                length = getattr(col.type, 'length', None)
                return f"VARCHAR({length})" if length else "VARCHAR(255)"
            elif t in (Text,):
                return "TEXT"
            elif t in (Integer,):
                return "INTEGER"
            elif t in (Float,):
                return "DOUBLE PRECISION"
            elif t in (Boolean,):
                return "BOOLEAN"
            elif t in (DateTime,):
                return "TIMESTAMP"
            elif t in (Date,):
                return "DATE"
            elif t in (Time,):
                return "TIME"
            elif t in (JSON,):
                return "JSON"
            return "TEXT"

        added_count = 0
        with engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                try:
                    existing_cols = {c['name'] for c in inspector.get_columns(table.name)}
                except Exception:
                    continue
                for col in table.columns:
                    if col.name not in existing_cols:
                        col_type = sa_type_to_sql(col)
                        default_clause = ""
                        if col.default is not None and col.default.arg is not None and not callable(col.default.arg):
                            dval = col.default.arg
                            if isinstance(dval, bool):
                                default_clause = f" DEFAULT {'TRUE' if dval else 'FALSE'}"
                            elif isinstance(dval, (int, float)):
                                default_clause = f" DEFAULT {dval}"
                            elif isinstance(dval, str):
                                default_clause = f" DEFAULT '{dval}'"
                        sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{default_clause}'
                        try:
                            conn.execute(_sql_text(sql))
                            added_count += 1
                            print(f"  + {table.name}.{col.name} ({col_type})")
                        except Exception:
                            pass
            conn.commit()
        print(f"Auto-migration complete: {added_count} columns added.")
    except Exception as e:
        print(f"Auto-migration error: {e}")
        import traceback
        traceback.print_exc()

    # ============================================================
    # PRODUCTION MODE — Ensure admin account exists, no demo seeding
    # ============================================================
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.email == "sunbullsolar@gmail.com").first()
        if admin_user:
            from app.auth import hash_password as _hp, verify_password as _vp
            if not _vp("admin123", admin_user.hashed_password):
                admin_user.hashed_password = _hp("admin123")
                db.commit()
                print("Fixed admin password hash.")
            else:
                print("Admin password hash OK.")
        else:
            from app.auth import hash_password
            admin = User(
                email="sunbullsolar@gmail.com",
                hashed_password=hash_password("admin123"),
                full_name="Abdo Yaghi",
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print("Admin account created.")

        print("Startup complete. Production mode — no demo data seeded.")
    except Exception as e:
        db.rollback()
        print(f"Startup error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


# ---- STATIC FILES & FRONTEND ----
# Three frontends: customer site (/), command center (/app), legacy (/legacy)
base_path = Path(__file__).parent.parent

# Frontend paths
frontend_site_path = base_path / "frontend-site" / "templates"
frontend_app_path = base_path / "frontend-app" / "templates"
legacy_frontend_path = base_path / "frontend" / "templates"

# Static files (shared)
static_path = base_path / "frontend" / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
async def root():
    """Serve the customer-facing website."""
    # Try new frontend-site first, fall back to legacy
    site_index = frontend_site_path / "index.html"
    if site_index.exists():
        return FileResponse(site_index)
    legacy_index = legacy_frontend_path / "index.html"
    if legacy_index.exists():
        return FileResponse(legacy_index)
    return {"message": "Sunbull OS API running. Frontend not found."}


@app.get("/app")
async def command_center():
    """Serve the Command Center (admin/rep dashboard)."""
    app_index = frontend_app_path / "index.html"
    if app_index.exists():
        return FileResponse(app_index)
    # Fall back to legacy frontend
    legacy_index = legacy_frontend_path / "index.html"
    if legacy_index.exists():
        return FileResponse(legacy_index)
    return {"message": "Command Center not found."}


@app.get("/register")
async def register_page():
    """Serve the rep registration page."""
    reg_file = frontend_app_path / "register.html"
    if reg_file.exists():
        return FileResponse(reg_file)
    return {"message": "Registration page not found."}


@app.get("/submit")
async def submit_appointment_page():
    """Serve the public appointment submission form for telemarketing/canvassers."""
    submit_file = frontend_app_path / "submit.html"
    if submit_file.exists():
        return FileResponse(submit_file)
    return {"message": "Submission form not found."}


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
