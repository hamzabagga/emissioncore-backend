from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AdminUser, DbSession, ReadUser
from app.core.errors import not_found
from app.models import Job
from app.schemas.job import JobOut

router = APIRouter(tags=["jobs"])


@router.post("/emission-factors/recalculate", response_model=JobOut, status_code=202)
def recalculate_records(admin: AdminUser, db: DbSession):
    """Recalcule en tâche de fond les records de l'organisation avec les facteurs
    actuels. À appeler après l'ajout ou la correction d'un facteur. Idempotent."""
    job = Job(organization_id=admin.organization_id, created_by=admin.id, type="recalculation")
    db.add(job)
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
