"""Recalcul des records après l'ajout ou la correction d'un facteur."""
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Job

# Une seule requête UPDATE ... FROM : PostgreSQL rattache chaque record au facteur
# valide à SA date (même source, même unité, même organisation) et recalcule kg_co2e.
# Idempotent : on ne touche que les records dont le facteur ou le montant change,
# donc relancer le job une 2e fois ne modifie plus rien.
RECALCULATE_SQL = text("""
    UPDATE emission_records AS r
    SET factor_id = f.id,
        kg_co2e = ROUND(r.activity_amount * f.kg_co2e_per_unit, 6),
        updated_at = now()
    FROM emission_factors AS f
    WHERE r.organization_id = :org_id
      AND f.organization_id = r.organization_id
      AND f.source = r.source
      AND f.unit = r.unit
      AND f.valid_from <= r.date
      AND (f.valid_to IS NULL OR r.date < f.valid_to)
      AND (r.factor_id <> f.id
           OR r.kg_co2e <> ROUND(r.activity_amount * f.kg_co2e_per_unit, 6))
      -- au-delà de Numeric(20, 6), Postgres refuserait TOUT l'UPDATE (job "failed")
      AND ROUND(r.activity_amount * f.kg_co2e_per_unit, 6) < 1e14
""")

# Records qu'on ne peut pas recalculer : kg_co2e dépasserait la colonne Numeric(20, 6).
# Laissés inchangés et signalés dans le rapport du job.
OVERFLOW_SQL = text("""
    SELECT count(*) FROM emission_records AS r
    JOIN emission_factors AS f
      ON f.organization_id = r.organization_id
     AND f.source = r.source AND f.unit = r.unit
     AND f.valid_from <= r.date
     AND (f.valid_to IS NULL OR r.date < f.valid_to)
    WHERE r.organization_id = :org_id
      AND ROUND(r.activity_amount * f.kg_co2e_per_unit, 6) >= 1e14
""")

# Records qui n'ont plus aucun facteur valide (ex. période d'un facteur raccourcie) :
# on ne les modifie pas, on les signale dans le rapport du job.
UNMATCHED_SQL = text("""
    SELECT count(*) FROM emission_records AS r
    WHERE r.organization_id = :org_id
      AND NOT EXISTS (
          SELECT 1 FROM emission_factors AS f
          WHERE f.organization_id = r.organization_id
            AND f.source = r.source AND f.unit = r.unit
            AND f.valid_from <= r.date
            AND (f.valid_to IS NULL OR r.date < f.valid_to)
      )
""")


def enqueue_recalculation(db: Session, organization_id: int, user_id: int) -> Job:
    """Ajoute un job de recalcul à la session (validé avec la transaction de l'appelant).

    Si un job de recalcul est déjà en attente pour l'organisation, on le réutilise :
    il lira les facteurs à jour quand il démarrera. Le verrou (SKIP LOCKED) empêche le
    worker de le démarrer avant la fin de notre transaction ; si le worker l'a déjà pris,
    il est sauté et on crée un nouveau job.
    """
    job = db.scalar(
        select(Job)
        .where(
            Job.organization_id == organization_id,
            Job.type == "recalculation",
            Job.status == "pending",
        )
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        job = Job(organization_id=organization_id, created_by=user_id, type="recalculation")
        db.add(job)
    return job


def process_recalculation_job(db: Session, job: Job) -> None:
    params = {"org_id": job.organization_id}
    updated = db.execute(RECALCULATE_SQL, params).rowcount
    unmatched = db.scalar(UNMATCHED_SQL, params)
    overflow = db.scalar(OVERFLOW_SQL, params)

    job.success_rows = updated
    job.error_rows = unmatched + overflow
    messages = []
    if unmatched:
        messages.append(
            f"{unmatched} record(s) have no valid emission factor anymore and were left unchanged"
        )
    if overflow:
        messages.append(
            f"{overflow} record(s) would have a kg_co2e too large to store and were left unchanged"
        )
    job.error_message = "; ".join(messages) or None
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()  # l'UPDATE et le statut du job sont validés ensemble
