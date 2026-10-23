from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import Organization, User

DEMO_PASSWORD = "password123"
DEMO_DATA = {
    "GreenAudit": [
        ("admin@greenaudit.io", "admin"),
        ("analyst@greenaudit.io", "analyst"),
        ("viewer@greenaudit.io", "viewer"),
    ],
    "EcoConseil": [
        ("admin@ecoconseil.io", "admin"),
    ],
}


def run() -> None:
    with SessionLocal() as db:
        for org_name, users in DEMO_DATA.items():
            org = db.scalar(select(Organization).where(Organization.name == org_name))
            if org is None:
                org = Organization(name=org_name)
                db.add(org)
                db.flush()  # récupère org.id sans encore valider la transaction
            for email, role in users:
                if db.scalar(select(User).where(User.email == email)) is None:
                    db.add(
                        User(
                            email=email,
                            role=role,
                            organization_id=org.id,
                            password_hash=hash_password(DEMO_PASSWORD),
                        )
                    )
        db.commit()
    print("Seed done")


if __name__ == "__main__":
    run()