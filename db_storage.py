import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def enabled():
    return bool(DATABASE_URL)


def _connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL není nastavené.")
    return psycopg2.connect(DATABASE_URL)


def ensure_schema():
    if not enabled():
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
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vehicles_spz ON vehicles ((data->>'spz'))"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vehicles_vin ON vehicles ((data->>'vin'))"
            )


def _seed_if_empty(seed_file):
    if not enabled() or not seed_file:
        return
    seed_path = Path(seed_file)
    if not seed_path.exists():
        return

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vehicles")
            count = cur.fetchone()[0]
            if count:
                return

            vehicles = json.loads(seed_path.read_text(encoding="utf-8"))
            for vehicle in vehicles:
                vehicle_id = str(vehicle.get("id") or "").strip()
                if not vehicle_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO vehicles (id, data, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE
                    SET data = EXCLUDED.data, updated_at = NOW()
                    """,
                    (vehicle_id, Json(vehicle)),
                )


def load_vehicles(seed_file=None):
    ensure_schema()
    _seed_if_empty(seed_file)
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
    ensure_schema()
    ids = []
    with _connect() as conn:
        with conn.cursor() as cur:
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
    if not enabled():
        return None
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vehicles")
            return cur.fetchone()[0]
