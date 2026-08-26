import os
import json
import hmac
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

SERVICE_VINS = [
    ("vc-sm023709", "TMBAG8NX5SM023709"),
    ("vc-sm022935", "TMBAG8NX9SM022935"),
]


def _seed_vehicle(vehicle_id, vin):
    return {
        "id": vehicle_id,
        "fleet": "vanscentre",
        "spz": "",
        "vehicle_id": "",
        "name": "Služební vozidlo",
        "brand": "ŠKODA",
        "model": "",
        "vin": vin,
        "year": "",
        "km": "",
        "status": "V provozu",
        "stk_until": "",
        "vignette_until": "",
        "liability_until": "",
        "casco_until": "",
        "assistance_until": "",
        "next_service_date": "",
        "next_service_km": "",
        "note": "",
        "photo": "",
        "documents": [],
        "service_records": [],
    }


def _configured_users(core):
    raw = os.environ.get("SERVICE_USERS", "").strip()
    users = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                users = {str(k).strip(): str(v) for k, v in parsed.items() if str(k).strip()}
        except Exception:
            for item in raw.split(","):
                if ":" in item:
                    username, password = item.split(":", 1)
                    username = username.strip()
                    if username:
                        users[username] = password
    if not users:
        password = os.environ.get("SERVICE_PASSWORD", "").strip()
        if password:
            users = {"vanscentre": password}
        elif getattr(core, "ADMIN_PIN", ""):
            users = {"vanscentre": str(core.ADMIN_PIN)}
    return users


def _db_enabled():
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _connect():
    return psycopg2.connect(os.environ.get("DATABASE_URL", "").strip())


def _ensure_db_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS service_vehicles (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for vehicle_id, vin in SERVICE_VINS:
                cur.execute("SELECT 1 FROM service_vehicles WHERE id=%s", (vehicle_id,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO service_vehicles (id, data, updated_at) VALUES (%s, %s, NOW())",
                        (vehicle_id, Json(_seed_vehicle(vehicle_id, vin))),
                    )


def _local_path(core):
    return Path(core.APP_DIR) / "service_vehicles.json"


def _load(core):
    if _db_enabled():
        _ensure_db_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM service_vehicles ORDER BY id")
                return [row[0] for row in cur.fetchall()]
    path = _local_path(core)
    if not path.exists():
        vehicles = [_seed_vehicle(i, vin) for i, vin in SERVICE_VINS]
        path.write_text(json.dumps(vehicles, ensure_ascii=False, indent=2), encoding="utf-8")
        return vehicles
    return json.loads(path.read_text(encoding="utf-8"))


def _save(core, vehicles):
    if _db_enabled():
        _ensure_db_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                for vehicle in vehicles:
                    cur.execute(
                        """
                        INSERT INTO service_vehicles (id, data, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data, updated_at=NOW()
                        """,
                        (str(vehicle["id"]), Json(vehicle)),
                    )
        return
    _local_path(core).write_text(json.dumps(vehicles, ensure_ascii=False, indent=2), encoding="utf-8")


def _hydrate(core, vehicles):
    changed = False
    for vehicle in vehicles:
        vin = str(vehicle.get("vin") or "").strip().upper()
        if len(vin) != 17 or vehicle.get("datova_kostka"):
            continue
        try:
            dk = core.make_datova_kostka(vin, core.fetch_datova_kostka(vin))
            basic = dk.get("basic") or {}
            vehicle["datova_kostka"] = dk
            vehicle["spz"] = basic.get("spz") or vehicle.get("spz") or ""
            vehicle["brand"] = basic.get("brand") or vehicle.get("brand") or ""
            vehicle["model"] = basic.get("model") or vehicle.get("model") or ""
            vehicle["name"] = basic.get("model") or vehicle.get("name") or "Služební vozidlo"
            vehicle["year"] = str(basic.get("year") or vehicle.get("year") or "")
            vehicle["stk_until"] = str(basic.get("inspection_until") or vehicle.get("stk_until") or "")[:10]
            changed = True
        except Exception as exc:
            print(f"Service vehicle Datová kostka failed for {vin}: {exc}")
    if changed:
        _save(core, vehicles)
    return vehicles


def _find(core, vehicle_id):
    return next((v for v in _load(core) if str(v.get("id")) == str(vehicle_id)), None)


def register(core):
    app = core.app

    def service_logged():
        return bool(core.session.get("service_user"))

    @app.route("/sluzebni/prihlaseni", methods=["GET", "POST"])
    def service_login():
        if service_logged():
            return core.redirect(core.url_for("service_index"))
        if core.request.method == "POST":
            username = (core.request.form.get("username") or "").strip()
            password = core.request.form.get("password") or ""
            users = _configured_users(core)
            expected = users.get(username)
            if expected is not None and hmac.compare_digest(str(expected), str(password)):
                core.session["service_user"] = username
                return core.redirect(core.url_for("service_index"))
            core.flash("Nesprávné uživatelské jméno nebo heslo.")
        return core.render_template("service_login.html")

    @app.route("/sluzebni/odhlasit")
    def service_logout():
        core.session.pop("service_user", None)
        return core.redirect(core.url_for("index"))

    @app.route("/sluzebni")
    def service_index():
        if not service_logged():
            return core.redirect(core.url_for("service_login"))
        vehicles = _hydrate(core, _load(core))
        return core.render_template(
            "service_index.html",
            vehicles=vehicles,
            service_user=core.session.get("service_user"),
        )

    @app.route("/sluzebni/v/<vehicle_id>")
    def service_vehicle(vehicle_id):
        if not service_logged():
            return core.redirect(core.url_for("service_login"))
        vehicles = _hydrate(core, _load(core))
        vehicle = next((v for v in vehicles if str(v.get("id")) == str(vehicle_id)), None)
        if not vehicle:
            return core.render_template("not_found.html", vehicle_id=vehicle_id), 404
        return core.render_template("service_vehicle.html", v=vehicle)

    @app.route("/sluzebni/v/<vehicle_id>/obnovit", methods=["POST"])
    def service_vehicle_refresh(vehicle_id):
        if not service_logged():
            return core.redirect(core.url_for("service_login"))
        vehicles = _load(core)
        vehicle = next((v for v in vehicles if str(v.get("id")) == str(vehicle_id)), None)
        if not vehicle:
            return core.render_template("not_found.html", vehicle_id=vehicle_id), 404
        vehicle.pop("datova_kostka", None)
        _save(core, vehicles)
        _hydrate(core, vehicles)
        return core.redirect(core.url_for("service_vehicle", vehicle_id=vehicle_id))
