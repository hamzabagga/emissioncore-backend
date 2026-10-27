"""Import CSV : création du job (API) et traitement par lots (worker)."""
import csv
import hashlib
import io
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import EmissionFactor, EmissionRecord, Job

MAX_ROWS = 100_000
BATCH_SIZE = 5_000
MAX_REPORTED_ERRORS = 1_000  # le rapport reste lisible même si tout le fichier est faux
REQUIRED_COLUMNS = ("source", "unit", "activity_amount", "date")
SOURCES = {"electricity", "fuel", "travel", "other"}
KG_PRECISION = Decimal("0.000001")
# Limites des colonnes Numeric(18, 6) et Numeric(20, 6) : au-delà, Postgres
# refuse l'INSERT et c'est tout le lot (donc le job) qui échoue.
MAX_AMOUNT = Decimal("1e12")
MAX_KG = Decimal("1e14")


# ---------------------------------------------------------------- côté API

def create_import_job(
    db: Session, organization_id: int, company_id: int, user_id: int, content: bytes
) -> tuple[Job, bool]:
    """Crée le job, ou renvoie le job existant si ce fichier a déjà été envoyé.

    Retourne (job, created). Idempotent : même fichier + même company = même job.
    """
    try:
        header, row_count = _inspect_csv(content)
    except UnicodeDecodeError:
        raise AppError(422, "invalid_csv", "File must be UTF-8 encoded") from None
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise AppError(
            422, "invalid_csv", "Missing required columns", details={"missing": missing}
        )
    if row_count > MAX_ROWS:
        raise AppError(
            422, "too_many_rows", f"CSV has {row_count} rows, maximum is {MAX_ROWS}"
        )

    sha256 = hashlib.sha256(content).hexdigest()
    existing = _find_job_for_file(db, company_id, sha256)
    if existing:
        return existing, False

    job = Job(
        organization_id=organization_id,
        company_id=company_id,
        created_by=user_id,
        type="import",
        file_sha256=sha256,
        file_content=content,
        total_rows=row_count,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # Deux uploads simultanés du même fichier : la contrainte unique
        # (company_id, file_sha256) n'en laisse passer qu'un.
        db.rollback()
        return _find_job_for_file(db, company_id, sha256), False
    db.refresh(job)
    return job, True


def _find_job_for_file(db: Session, company_id: int, sha256: str) -> Job | None:
    return db.scalar(
        select(Job).where(Job.company_id == company_id, Job.file_sha256 == sha256)
    )


def _inspect_csv(content: bytes) -> tuple[list[str], int]:
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig")))
    header = [h.strip() for h in next(reader, [])]
    row_count = sum(1 for row in reader if any(cell.strip() for cell in row))
    return header, row_count


# ------------------------------------------------------------- côté worker

class _FactorLookup:
    """Facteurs de l'organisation chargés UNE fois en mémoire (quelques dizaines),
    au lieu d'une requête SQL par ligne du CSV."""

    def __init__(self, db: Session, organization_id: int):
        self._by_key: dict[tuple[str, str], list[EmissionFactor]] = {}
        factors = db.scalars(
            select(EmissionFactor).where(EmissionFactor.organization_id == organization_id)
        )
        for f in factors:
            self._by_key.setdefault((f.source, f.unit), []).append(f)

    def find(self, source: str, unit: str, on_date: date) -> EmissionFactor | None:
        for f in self._by_key.get((source, unit), []):
            if f.valid_from <= on_date and (f.valid_to is None or on_date < f.valid_to):
                return f
        return None


def _parse_row(row: dict, factors: _FactorLookup) -> tuple[dict | None, str | None]:
    """Valide une ligne. Retourne (valeurs, None) ou (None, raison de l'erreur)."""
    source = (row.get("source") or "").strip()
    unit = (row.get("unit") or "").strip()
    raw_amount = (row.get("activity_amount") or "").strip()
    raw_date = (row.get("date") or "").strip()

    if source not in SOURCES:
        return None, f"invalid source '{source}'"
    if not unit:
        return None, "unit is required"
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        return None, f"invalid activity_amount '{raw_amount}'"
    if not amount.is_finite() or amount < 0:
        return None, "activity_amount must be a number >= 0"
    if amount >= MAX_AMOUNT:
        return None, f"activity_amount must be < {MAX_AMOUNT:f}"
    # arrondi à 6 décimales comme Postgres (sûr ici : amount < 1e12)
    amount = amount.quantize(KG_PRECISION, rounding=ROUND_HALF_UP)
    if amount >= MAX_AMOUNT:  # ex. 999999999999.9999999 arrondi -> 1e12
        return None, f"activity_amount must be < {MAX_AMOUNT:f}"
    try:
        on_date = date.fromisoformat(raw_date)
    except ValueError:
        return None, f"invalid date '{raw_date}' (expected YYYY-MM-DD)"

    factor = factors.find(source, unit, on_date)
    if factor is None:
        return None, f"no emission factor valid on {on_date} for {source}/{unit}"

    kg = amount * factor.kg_co2e_per_unit
    if kg >= MAX_KG:  # vérifié avant quantize, qui lèverait InvalidOperation
        return None, "computed kg_co2e is too large"
    kg = kg.quantize(KG_PRECISION, rounding=ROUND_HALF_UP)
    if kg >= MAX_KG:  # l'arrondi peut atteindre la limite
        return None, "computed kg_co2e is too large"
    return {
        "source": source,
        "unit": unit,
        "activity_amount": amount,
        "date": on_date,
        "kg_co2e": kg,
        "factor_id": factor.id,
    }, None


def process_import_job(db: Session, job: Job) -> None:
    """Traite un job d'import. Peut être relancé sans créer de doublons."""
    factors = _FactorLookup(db, job.organization_id)
    reader = csv.DictReader(io.StringIO(job.file_content.decode("utf-8-sig")))
    reader.fieldnames = [h.strip() for h in reader.fieldnames or []]

    errors: list[dict] = []
    error_count = processed = 0
    batch: list[dict] = []

    def flush() -> None:
        if batch:
            # Insertion par lots ; ON CONFLICT : une ligne déjà importée
            # (job relancé après un crash) est simplement ignorée.
            db.execute(
                insert(EmissionRecord)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_records_import_row")
            )
            batch.clear()
        job.processed_rows = processed
        job.error_rows = error_count
        db.commit()  # progression visible via GET /imports/{id}

    # numéro de ligne = ligne du fichier (l'en-tête est la ligne 1)
    for line_number, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue  # ligne vide ignorée
        processed += 1
        values, error = _parse_row(row, factors)
        if error:
            error_count += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append({"row": line_number, "error": error})
            continue
        batch.append(
            {
                **values,
                "organization_id": job.organization_id,
                "company_id": job.company_id,
                "import_job_id": job.id,
                "import_row": line_number,
            }
        )
        if len(batch) >= BATCH_SIZE:
            flush()
    flush()

    job.success_rows = processed - error_count
    job.errors = errors
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    job.file_content = None  # le fichier n'est plus utile : on libère la place
    db.commit()


def mark_job_failed(db: Session, job_id: int, message: str) -> None:
    db.rollback()
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status="failed", error_message=message[:1000],
                finished_at=datetime.now(timezone.utc))
    )
    db.commit()
