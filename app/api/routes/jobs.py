from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AdminUser, DbSession, ReadUser
from app.core.errors import not_found
from app.models import Job
from app.schemas.job import JobOut
from app.services.recalculation import enqueue_recalculation

router = APIRouter(tags=["jobs"])


@router.post("/emission-factors/recalculate", response_model=JobOut, status_code=202)
def recalculate_records(admin: AdminUser, db: DbSession):
    """Recalcule en tâche de fond les records de l'organisation avec les facteurs
    actuels. Déjà lancé automatiquement après chaque création ou modification de
    facteur ; renvoie le job en attente s'il y en a un. Idempotent."""
    job = enqueue_recalculation(db, admin.organization_id, admin.id)
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, user: ReadUser, db: DbSession):
    job = db.scalar(
        select(Job).where(Job.id == job_id, Job.organization_id == user.organization_id)
    )
    if job is None:
        raise not_found("Job")
    return job
