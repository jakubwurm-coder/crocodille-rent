import os
from datetime import timezone

import psycopg2
from psycopg2.extras import Json

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

_SCHEMA_READY = False
_WRITE_LOCK_KEY = 764311221
_REQUIRED_TABLES = (
    "vehicles",
    "device_tokens",
    "notification_log",
    "job_runs",
)


def enabled():
    return bool(DATABASE_URL)


def require_enabled():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL není nastavené. CROCODILLE RENT používá výhradně PostgreSQL.")


def _connect():
    require_enabled()
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=8,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
    )


def _schema_exists():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('public.vehicles'),
                    to_regclass('public.device_tokens'),
                    to_regclass('public.notification_log'),
                    to_regclass('public.job_runs')
                """
            )
            row = cur.fetchone() or ()
            return len(row) == len(_REQUIRED_TABLES) and all(row)


def ensure_schema():
    """Jednorázově ověří databázové schéma bez blokování webových requestů.

    V běžném provozu už jsou tabulky vytvořené migrací. Pokud by některá chyběla,
    vytvoří se pouze při prvním startu procesu. Nepoužíváme Python thread lock,
    protože ten dříve mohl zablokovat Gunicorn worker během souběžného background jobu.
    """
    global _SCHEMA_READY
    require_enabled()
    if _SCHEMA_READY:
        return

    if _schema_exists():
        _SCHEMA_READY = True
        return

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS vehicles (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_spz ON vehicles ((data->>'spz'))")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_vin ON vehicles ((data->>'vin'))")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS device_tokens (
                    token TEXT PRIMARY KEY,
                    platform TEXT NOT NULL DEFAULT 'ios',
                    app_version TEXT NOT NULL DEFAULT '',
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_log (
                    token TEXT NOT NULL,
                    alert_key TEXT NOT NULL,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (token, alert_key)
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS job_runs (
                    job_key TEXT NOT NULL,
                    run_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ,
                    details JSONB,
                    PRIMARY KEY (job_key, run_key)
                )
                """
            )

    _SCHEMA_READY = True


def load_vehicles():
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT data
                FROM vehicles
                ORDER BY COALESCE(data->>'vehicle_id', ''), COALESCE(data->>'spz', ''), id
                """
            )
            return [row[0] for row in cur.fetchall()]


def save_vehicles(vehicles):
    """Atomicky uloží celý registr vozidel.

    PostgreSQL advisory lock serializuje souběžné zápisy webu a automatických
    synchronizací a brání dřívějším deadlockům při paralelních UPDATE/DELETE.
    """
    ensure_schema()
    ids = []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_WRITE_LOCK_KEY,))
            for vehicle in vehicles:
                vehicle_id = str(vehicle.get("id") or "").strip()
                if not vehicle_id:
                    raise ValueError("Každé vozidlo musí mít id.")
                ids.append(vehicle_id)
                cur.execute(
                    """
                    INSERT INTO vehicles (id, data, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE
                    SET data = EXCLUDED.data, updated_at = NOW()
                    """,
                    (vehicle_id, Json(vehicle)),
                )

            if ids:
                cur.execute("DELETE FROM vehicles WHERE NOT (id = ANY(%s))", (ids,))
            else:
                cur.execute("DELETE FROM vehicles")


def count_vehicles():
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vehicles")
            return int(cur.fetchone()[0])


def register_device_token(token, platform="ios", app_version=""):
    ensure_schema()
    token = str(token or "").strip().lower()
    if not token:
        raise ValueError("Chybí device token.")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO device_tokens (token, platform, app_version, enabled, created_at, last_seen_at)
                VALUES (%s, %s, %s, TRUE, NOW(), NOW())
                ON CONFLICT (token) DO UPDATE
                SET platform = EXCLUDED.platform,
                    app_version = EXCLUDED.app_version,
                    enabled = TRUE,
                    last_seen_at = NOW()
                """,
                (token, platform or "ios", app_version or ""),
            )


def disable_device_token(token):
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE device_tokens SET enabled=FALSE WHERE token=%s", (str(token or "").strip().lower(),))


def active_device_tokens():
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT token FROM device_tokens WHERE enabled=TRUE ORDER BY last_seen_at DESC")
            return [row[0] for row in cur.fetchall()]


def notification_was_sent(token, alert_key):
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM notification_log WHERE token=%s AND alert_key=%s",
                (token, alert_key),
            )
            return cur.fetchone() is not None


def mark_notification_sent(token, alert_key):
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notification_log (token, alert_key, sent_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (token, alert_key) DO NOTHING
                """,
                (token, alert_key),
            )


def claim_job(job_key, run_key, stale_after_minutes=90):
    """Vrátí True pouze procesu, který má daný běh skutečně provést."""
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_key, run_key, status, started_at, finished_at, details)
                VALUES (%s, %s, 'running', NOW(), NULL, NULL)
                ON CONFLICT (job_key, run_key) DO UPDATE
                SET status='running', started_at=NOW(), finished_at=NULL, details=NULL
                WHERE job_runs.status='failed'
                   OR (job_runs.status='running' AND job_runs.started_at < NOW() - make_interval(mins => %s))
                RETURNING job_key
                """,
                (job_key, run_key, int(stale_after_minutes)),
            )
            return cur.fetchone() is not None


def finish_job(job_key, run_key, status="ok", details=None):
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_runs
                SET status=%s, finished_at=NOW(), details=%s
                WHERE job_key=%s AND run_key=%s
                """,
                (status, Json(details or {}), job_key, run_key),
            )


def latest_job(job_key):
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_key, status, started_at, finished_at, details
                FROM job_runs
                WHERE job_key=%s
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (job_key,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "run_key": row[0],
                "status": row[1],
                "started_at": row[2].astimezone(timezone.utc).isoformat() if row[2] else None,
                "finished_at": row[3].astimezone(timezone.utc).isoformat() if row[3] else None,
                "details": row[4] or {},
            }
