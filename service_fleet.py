import os
import json
import hmac
import base64
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests
from psycopg2.extras import Json

SERVICE_VINS = [
    ("vc-sm023709", "TMBAG8NX5SM023709"),
    ("vc-sm022935", "TMBAG8NX9SM022935"),
]

# eDalnice – veřejné ověření platnosti podle země registrace + SPZ.
# Česká republika má v API tento country ID.
EDALNICE_COUNTRY_ID_CZ = "3906ba89-153c-4038-8e36-0ca1deb76076"
EDALNICE_INDEX_URLS = (
    "https://edalnice.gov.cz/",
    "https://edalnice.cz/",
)
EDALNICE_AUTH_URL = "https://auth.edalnice.cz/auth/connect/token"
EDALNICE_VALIDATION_URL = (
    "https://eshop.edalnice.cz/api/v3/charge_registrations/"
    + EDALNICE_COUNTRY_ID_CZ
    + "/"
)
VIGNETTE_REFRESH_SECONDS = int(os.environ.get("VIGNETTE_REFRESH_SECONDS", "43200"))
VIGNETTE_STALE_SECONDS = int(os.environ.get("VIGNETTE_STALE_SECONDS", "72000"))

_edalnice_token_cache = {"token": "", "expires_at": 0.0}
_vignette_refresh_lock = threading.Lock()
_vignette_last_started = 0.0


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


def _parse_iso(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _edalnice_client_credentials():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
    }
    last_error = None
    patterns = [
        (r'"REACT_APP_CLIENT_ID"\s*:\s*"([^"]+)"', r'"REACT_APP_CLIENT_SECRET"\s*:\s*"([^"]+)"'),
        (r'REACT_APP_CLIENT_ID["\']?\s*[:=]\s*["\']([^"\']+)', r'REACT_APP_CLIENT_SECRET["\']?\s*[:=]\s*["\']([^"\']+)'),
    ]
    for url in EDALNICE_INDEX_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            text = response.text
            for id_pattern, secret_pattern in patterns:
                client_id = re.search(id_pattern, text)
                client_secret = re.search(secret_pattern, text)
                if client_id and client_secret:
                    return client_id.group(1), client_secret.group(1)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"eDalnice: nepodařilo se načíst veřejné klientské údaje ({last_error or 'nenalezeny'}).")


def _edalnice_token(force=False):
    now = time.time()
    if not force and _edalnice_token_cache["token"] and _edalnice_token_cache["expires_at"] > now + 60:
        return _edalnice_token_cache["token"]

    client_id, client_secret = _edalnice_client_credentials()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    response = requests.post(
        EDALNICE_AUTH_URL,
        data={"grant_type": "client_credentials", "Scope": "eshop.api eshoppayment.api"},
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("eDalnice: autentizace nevrátila access_token.")
    expires_in = int(payload.get("expires_in") or 300)
    _edalnice_token_cache["token"] = token
    _edalnice_token_cache["expires_at"] = now + max(60, expires_in)
    return token


def _edalnice_lookup(spz):
    plate = re.sub(r"\s+", "", str(spz or "").strip().upper())
    if not plate:
        raise ValueError("Chybí SPZ.")

    def request_status(token):
        return requests.get(
            EDALNICE_VALIDATION_URL + plate,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json,text/json",
                "Accept-Language": "cs",
                "Origin": "https://edalnice.gov.cz",
                "Referer": "https://edalnice.gov.cz/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36",
            },
            timeout=20,
        )

    token = _edalnice_token()
    response = request_status(token)
    if response.status_code == 401:
        token = _edalnice_token(force=True)
        response = request_status(token)
    response.raise_for_status()
    payload = response.json()

    now = datetime.now(timezone.utc)
    current = []
    future = []
    for charge in payload.get("charges") or []:
        valid_since = _parse_iso(charge.get("validSince"))
        valid_until = _parse_iso(charge.get("validUntil"))
        if valid_since and valid_since.tzinfo is None:
            valid_since = valid_since.replace(tzinfo=timezone.utc)
        if valid_until and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        item = {
            "valid_since": valid_since,
            "valid_until": valid_until,
            "is_currently_valid": bool(charge.get("isCurrentlyValid")),
            "fuel_type": charge.get("fuelType"),
        }
        if item["is_currently_valid"] or (valid_since and valid_until and valid_since <= now <= valid_until):
            current.append(item)
        elif valid_since and valid_since > now:
            future.append(item)

    selected = None
    if current:
        selected = max(current, key=lambda x: x.get("valid_until") or datetime.min.replace(tzinfo=timezone.utc))
    elif future:
        selected = min(future, key=lambda x: x.get("valid_since") or datetime.max.replace(tzinfo=timezone.utc))

    return {
        "plate": plate,
        "is_valid": bool(current),
        "is_exempt": bool(payload.get("isGivenExemption")),
        "valid_since": selected.get("valid_since") if selected else None,
        "valid_until": selected.get("valid_until") if selected else None,
        "future_vignette": bool(selected and not current and future),
    }


def _checked_recently(vehicle):
    checked = _parse_iso(vehicle.get("vignette_checked_at"))
    if not checked:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - checked).total_seconds() < VIGNETTE_STALE_SECONDS


def _refresh_main_vignettes(core, force=False):
    if not _vignette_refresh_lock.acquire(blocking=False):
        return {"updated": 0, "errors": ["Kontrola už právě běží."]}
    try:
        vehicles = core.load_vehicles()
        changed = False
        updated = 0
        errors = []
        for vehicle in vehicles:
            spz = str(vehicle.get("spz") or "").strip().upper()
            if not spz or not core.is_active_vehicle(vehicle):
                continue
            if not force and _checked_recently(vehicle):
                continue
            try:
                result = _edalnice_lookup(spz)
                now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                vehicle["vignette_checked_at"] = now_iso
                vehicle["vignette_source"] = "edalnice"
                vehicle["vignette_is_exempt"] = result["is_exempt"]
                vehicle["vignette_future_from"] = (
                    result["valid_since"].date().isoformat()
                    if result.get("future_vignette") and result.get("valid_since")
                    else ""
                )

                if result["is_exempt"]:
                    vehicle["vignette_status"] = "exempt"
                    vehicle["vignette_until"] = ""
                elif result["is_valid"] and result.get("valid_until"):
                    vehicle["vignette_status"] = "valid"
                    vehicle["vignette_until"] = result["valid_until"].date().isoformat()
                elif result.get("future_vignette"):
                    vehicle["vignette_status"] = "future"
                    # Budoucí známku neukazujeme jako právě platnou.
                    vehicle["vignette_until"] = ""
                else:
                    vehicle["vignette_status"] = "missing"
                    vehicle["vignette_until"] = ""
                updated += 1
                changed = True
            except Exception as exc:
                errors.append(f"{spz}: {type(exc).__name__}: {exc}")
                vehicle["vignette_last_error"] = str(exc)[:300]
                vehicle["vignette_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                changed = True

        if changed:
            core.save_vehicles(vehicles, f"Automatická kontrola dálničních známek ({updated} vozidel)")
        return {"updated": updated, "errors": errors}
    finally:
        _vignette_refresh_lock.release()


def _maybe_start_vignette_refresh(core):
    global _vignette_last_started
    now = time.time()
    if now - _vignette_last_started < VIGNETTE_REFRESH_SECONDS:
        return
    _vignette_last_started = now

    def worker():
        try:
            result = _refresh_main_vignettes(core, force=False)
            if result.get("errors"):
                print("eDalnice refresh errors:", "; ".join(result["errors"]))
            else:
                print(f"eDalnice refresh OK: {result.get('updated', 0)} vozidel")
        except Exception as exc:
            print(f"eDalnice background refresh failed: {exc}")

    threading.Thread(target=worker, name="edalnice-refresh", daemon=True).start()


def register(core):
    app = core.app

    # Automatická kontrola běží mimo obsluhu stránky, takže nebrzdí otevření webu.
    @app.before_request
    def auto_vignette_refresh():
        _maybe_start_vignette_refresh(core)

    @app.route("/admin/vignette-refresh", methods=["POST"])
    def admin_vignette_refresh():
        if not core.require_admin():
            return core.redirect(core.url_for("login"))
        result = _refresh_main_vignettes(core, force=True)
        if result["errors"]:
            core.flash(
                f"Dálniční známky: aktualizováno {result['updated']} vozidel, chyb {len(result['errors'])}. "
                + " | ".join(result["errors"][:3])
            )
        else:
            core.flash(f"Dálniční známky byly ověřeny pro {result['updated']} vozidel.")
        return core.redirect(core.url_for("admin"))

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
