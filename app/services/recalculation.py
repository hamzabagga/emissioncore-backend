"""Recalcul des records après l'ajout ou la correction d'un facteur."""
from datetime import datetime, timezone

from sqlalchemy import text
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


def process_recalculation_job(db: Session, job: Job) -> None:
    params = {"org_id": job.organization_id}
    updated = db.execute(RECALCULATE_SQL, params).rowcount
    unmatched = db.scalar(UNMATCHED_SQL, params)

    job.success_rows = updated
    job.error_rows = unmatched
    if unmatched:
        job.error_message = (
            f"{unmatched} record(s) have no valid emission factor anymore and were left unchanged"
        )
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()  # l'UPDATE et le statut du job sont validés ensemble
