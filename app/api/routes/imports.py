from fastapi import APIRouter, File, Response, UploadFile
from sqlalchemy import select

from app.api.deps import DbSession, ReadUser, WriteUser
from app.api.scoping import get_company_or_404
from app.core.errors import AppError, not_found
from app.models import Job
from app.schemas.job import JobOut
from app.services.imports import create_import_job

router = APIRouter(tags=["imports"])

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 Mo : largement assez pour 100 000 lignes


@router.post("/companies/{company_id}/records/import", response_model=JobOut, status_code=202)
def import_records(
    company_id: int,
    user: WriteUser,
    db: DbSession,
    response: Response,
    file: UploadFile = File(...),
):
    get_company_or_404(db, company_id, user.organization_id)
    content = file.file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise AppError(413, "file_too_large", "File is larger than 20 MB")
    job, created = create_import_job(db, user.organization_id, company_id, user.id, content)
    if not created:
        response.status_code = 200  # même fichier déjà envoyé : on renvoie le job existant
    return job


@router.get("/imports/{job_id}", response_model=JobOut)
def get_import(job_id: int, user: ReadUser, db: DbSession):
    job = db.scalar(
        select(Job).where(
            Job.id == job_id, Job.organization_id == user.organization_id, Job.type == "import"
        )
    )
    if job is None:
        raise not_found("Import job")
    return job
