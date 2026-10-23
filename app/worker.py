"""Worker de tâches de fond : `python -m app.worker`.

Il lit la table `jobs` dans PostgreSQL. Plusieurs workers peuvent tourner en
parallèle : FOR UPDATE SKIP LOCKED garantit qu'un job n'est pris que par un seul.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Job
from app.services.imports import mark_job_failed, process_import_job

logger = logging.getLogger("worker")

POLL_SECONDS = 1.0
# Un job "processing" depuis trop longtemps = worker mort en route -> on le reprend.
# Sans danger : le traitement est idempotent (ON CONFLICT DO NOTHING).
STALE_AFTER = timedelta(minutes=10)

HANDLERS = {
    "import": process_import_job,
}


def claim_next_job(db: Session) -> Job | None:
    stale_limit = datetime.now(timezone.utc) - STALE_AFTER
    job = db.scalar(
        select(Job)
        .where(
            or_(
                Job.status == "pending",
                (Job.status == "processing") & (Job.started_at < stale_limit),
            )
        )
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = "processing"
    job.started_at = datetime.now(timezone.utc)
    db.commit()  # libère le verrou ; le statut "processing" protège le job
    return job


def run_once(db: Session) -> bool:
    """Traite au plus un job. Retourne True si un job a été traité."""
    job = claim_next_job(db)
    if job is None:
        return False
    logger.info("processing job %s (%s)", job.id, job.type)
    try:
        HANDLERS[job.type](db, job)
    except Exception as exc:  # un job en erreur ne doit pas arrêter le worker
        logger.exception("job %s failed", job.id)
        mark_job_failed(db, job.id, str(exc))
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("worker started")
    while True:
        try:
            with SessionLocal() as db:
                worked = run_once(db)
        except Exception:  # ex. base pas encore prête au démarrage
            logger.exception("worker loop error")
            worked = False
        if not worked:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
