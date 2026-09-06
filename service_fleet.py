import os
import json
import hmac
import base64
import re
import threading
import time
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urljoin

import psycopg2
import requests
from psycopg2.extras import Json

SERVICE_VINS = [
    ("vc-sm023709", "TMBAG8NX5SM023709"),
    ("vc-sm022935", "TMBAG8NX9SM022935"),
]

# eDalnice – veřejné ověření platnosti podle země registrace + SPZ.
# Aktuální frontend (09/2026) používá OAuth client_credentials a Bearer token.
EDALNICE_COUNTRY_ID_CZ = "3906ba89-153c-4038-8e36-0ca1deb76076"
EDALNICE_INDEX_URL = "https://edalnice.gov.cz/"
EDALNICE_AUTH_URL = "https://auth.edalnice.gov.cz/auth/connect/token"
EDALNICE_VALIDATION_URL = (
    "https://eshop.edalnice.gov.cz/api/v3/charge_registrations/"
    + EDALNICE_COUNTRY_ID_CZ
    + "/"
)
VIGNETTE_REFRESH_SECONDS = int(os.environ.get("VIGNETTE_REFRESH_SECONDS", "43200"))
VIGNETTE_STALE_SECONDS = int(os.environ.get("VIGNETTE_STALE_SECONDS", "72000"))
VIGNETTE_CACHE_WINDOW_DAYS = int(os.environ.get("VIGNETTE_CACHE_WINDOW_DAYS", "30"))
VIGNETTE_CACHE_NEAR_DAYS = int(os.environ.get("VIGNETTE_CACHE_NEAR_DAYS", "7"))
VIGNETTE_CACHE_DAILY_SECONDS = int(os.environ.get("VIGNETTE_CACHE_DAILY_SECONDS", "86400"))
VIGNETTE_CACHE_NEAR_SECONDS = int(os.environ.get("VIGNETTE_CACHE_NEAR_SECONDS", "21600"))
VIGNETTE_CACHE_EXPIRED_SECONDS = int(os.environ.get("VIGNETTE_CACHE_EXPIRED_SECONDS", "3600"))

_edalnice_token_cache = {"token": "", "expires_at": 0.0}
_edalnice_client_cache = {"client_id": "", "client_secret": "", "expires_at": 0.0}
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


def _parse_date(value):
    raw = str(value or "").strip()[:10]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


def _edalnice_browser_headers(accept="*/*"):
    return {
        "Accept": accept,
        "Accept-Language": "cs",
        "Referer": "https://edalnice.gov.cz/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    }


def _edalnice_client_credentials(force=False):
    """Načte veřejný OAuth client z aktuálního JS bundle eDalnice."""
    now = time.time()
    if (
        not force
        and _edalnice_client_cache["client_id"]
        and _edalnice_client_cache["client_secret"]
        and _edalnice_client_cache["expires_at"] > now
    ):
        return _edalnice_client_cache["client_id"], _edalnice_client_cache["client_secret"]

    env_pair = os.environ.get("EDALNICE_CLIENT_BASIC", "").strip()
    if ":" in env_pair:
        client_id, client_secret = env_pair.split(":", 1)
        if client_id and client_secret:
            _edalnice_client_cache.update({
                "client_id": client_id,
                "client_secret": client_secret,
                "expires_at": now + 86400,
            })
            return client_id, client_secret

    response = requests.get(
        EDALNICE_INDEX_URL,
        headers=_edalnice_browser_headers("text/html,application/xhtml+xml"),
        timeout=20,
    )
    response.raise_for_status()

    html = response.text
    script_urls = []
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        url = urljoin(EDALNICE_INDEX_URL, src)
        if url not in script_urls:
            script_urls.append(url)

    script_urls = script_urls[:80]
    patterns = [
        r'["\'](eshop\.client):([^"\']+)["\']',
        r'\b(eshop\.client):([A-Za-z0-9._~!*()\-]+)',
    ]

    last_error = None
    for script_url in script_urls:
        try:
            script = requests.get(
                script_url,
                headers=_edalnice_browser_headers("*/*"),
                timeout=20,
            )
            script.raise_for_status()
            text = script.text
            if "auth.edalnice.gov.cz/auth/connect/token" not in text and "eshop.client" not in text:
                continue
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    client_id = match.group(1)
                    client_secret = match.group(2)
                    _edalnice_client_cache.update({
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "expires_at": now + 21600,
                    })
                    return client_id, client_secret
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "eDalnice: v aktuálních JS bundle nebyl nalezen veřejný OAuth klient"
        + (f" ({last_error})" if last_error else "")
    )


def _edalnice_token(force=False):
    now = time.time()
    if (
        not force
        and _edalnice_token_cache["token"]
        and _edalnice_token_cache["expires_at"] > now + 60
    ):
        return _edalnice_token_cache["token"]

    client_id, client_secret = _edalnice_client_credentials(force=force)
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")

    response = requests.post(
        EDALNICE_AUTH_URL,
        data={
            "grant_type": "client_credentials",
            "scope": "eshop.api",
        },
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": _edalnice_browser_headers()["User-Agent"],
        },
        timeout=20,
    )

    if not response.ok:
        body = (response.text or "")[:500].replace("\n", " ")
        raise RuntimeError(f"eDalnice OAuth HTTP {response.status_code}: {body}")

    payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("eDalnice: OAuth odpověď neobsahuje access_token.")

    expires_in = int(payload.get("expires_in") or 300)
    _edalnice_token_cache["token"] = token
    _edalnice_token_cache["expires_at"] = now + max(60, expires_in)
    return token


def _edalnice_collect_charge_dicts(node):
    """Najde všechny záznamy známek v odpovědi eDalnice, i když jsou v jiné vnořené sekci."""
    found = []
    if isinstance(node, dict):
        has_since = "validSince" in node or "valid_since" in node
        has_until = "validUntil" in node or "valid_until" in node
        if has_since and has_until:
            found.append(node)
        for value in node.values():
            found.extend(_edalnice_collect_charge_dicts(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_edalnice_collect_charge_dicts(value))
    return found


def _edalnice_lookup(spz):
    plate = re.sub(r"\s+", "", str(spz or "").strip().upper())
    if not plate:
        raise ValueError("Chybí SPZ.")

    def request_status(token):
        headers = _edalnice_browser_headers("*/*")
        headers.update({
            "Authorization": f"Bearer {token}",
            "Origin": "https://edalnice.gov.cz",
        })
        return requests.get(
            EDALNICE_VALIDATION_URL + plate,
            headers=headers,
            timeout=20,
        )

    token = _edalnice_token()
    response = request_status(token)

    if response.status_code == 401:
        _edalnice_token_cache["token"] = ""
        _edalnice_token_cache["expires_at"] = 0.0
        token = _edalnice_token(force=True)
        response = request_status(token)

    if not response.ok:
        body = (response.text or "")[:500].replace("\n", " ")
        raise RuntimeError(
            f"eDalnice API HTTP {response.status_code} pro {plate}: {body}"
        )

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"eDalnice API pro {plate} nevrátilo platné JSON: {exc}") from exc

    now = datetime.now(timezone.utc)
    current = []
    future = []
    all_items = []

    for charge in _edalnice_collect_charge_dicts(payload):
        valid_since = _parse_iso(charge.get("validSince") or charge.get("valid_since"))
        valid_until = _parse_iso(charge.get("validUntil") or charge.get("valid_until"))
        if valid_since and valid_since.tzinfo is None:
            valid_since = valid_since.replace(tzinfo=timezone.utc)
        if valid_until and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if not valid_since or not valid_until:
            continue

        item = {
            "valid_since": valid_since,
            "valid_until": valid_until,
            "is_currently_valid": bool(charge.get("isCurrentlyValid") or charge.get("is_currently_valid")),
            "fuel_type": charge.get("fuelType") or charge.get("fuel_type"),
        }
        all_items.append(item)

        if item["is_currently_valid"] or valid_since <= now <= valid_until:
            current.append(item)
        elif valid_since > now:
            future.append(item)

    unique = {}
    for item in all_items:
        key = (item["valid_since"].isoformat(), item["valid_until"].isoformat())
        unique[key] = item
    all_items = list(unique.values())

    selected = None
    if current:
        candidates = [item for item in all_items if item.get("valid_until") and item["valid_until"] >= now]
        if candidates:
            selected = max(candidates, key=lambda x: x["valid_until"])
        else:
            selected = max(current, key=lambda x: x.get("valid_until") or datetime.min.replace(tzinfo=timezone.utc))
    elif future:
        selected = min(future, key=lambda x: x.get("valid_since") or datetime.max.replace(tzinfo=timezone.utc))

    return {
        "plate": plate,
        "is_valid": bool(current),
        "is_exempt": bool(payload.get("isGivenExemption") or payload.get("is_given_exemption")),
        "valid_since": selected.get("valid_since") if selected else None,
        "valid_until": selected.get("valid_until") if selected else None,
        "future_vignette": bool(selected and not current and future),
    }


def _vignette_refresh_due(vehicle, force=False):
    """Databáze je primární zdroj; eDalnice se volá jen když je potřeba."""
    if force:
        return True

    source = str(vehicle.get("vignette_source") or "").strip()
    checked = _parse_iso(vehicle.get("vignette_checked_at"))
    if checked and checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)

    # Nové vozidlo nebo starý ruční záznam se ověří okamžitě.
    if not source or not checked:
        return True

    age_seconds = max(0.0, (datetime.now(timezone.utc) - checked).total_seconds())
    until = _parse_date(vehicle.get("vignette_until"))

    # Ověřená chybějící/budoucí známka bez uloženého konce: 1x denně.
    if not until:
        return age_seconds >= VIGNETTE_CACHE_DAILY_SECONDS

    days_left = (until - date.today()).days
    if days_left > VIGNETTE_CACHE_WINDOW_DAYS:
        return False
    if days_left > VIGNETTE_CACHE_NEAR_DAYS:
        return age_seconds >= VIGNETTE_CACHE_DAILY_SECONDS
    if days_left >= 0:
        return age_seconds >= VIGNETTE_CACHE_NEAR_SECONDS
    return age_seconds >= VIGNETTE_CACHE_EXPIRED_SECONDS


def _checked_recently(vehicle):
    # Zachováno kvůli kompatibilitě se starším voláním; nově je rozhodující
    # _vignette_refresh_due a datum expirace uložené v PostgreSQL.
    return not _vignette_refresh_due(vehicle, force=False)


def _refresh_main_vignettes(core, force=False):
    if not _vignette_refresh_lock.acquire(blocking=False):
        return {"updated": 0, "skipped": 0, "errors": ["Kontrola už právě běží."]}
    try:
        vehicles = core.load_vehicles()
        changed = False
        updated = 0
        skipped = 0
        errors = []
        for vehicle in vehicles:
            spz = str(vehicle.get("spz") or "").strip().upper()
            if not spz or not core.is_active_vehicle(vehicle):
                continue
            if not _vignette_refresh_due(vehicle, force=force):
                skipped += 1
                continue
            try:
                result = _edalnice_lookup(spz)
                now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                vehicle["vignette_checked_at"] = now_iso
                vehicle["vignette_source"] = "edalnice"
                vehicle["vignette_is_exempt"] = result["is_exempt"]
                vehicle.pop("vignette_last_error", None)
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
                    vehicle["vignette_until"] = ""
                    vehicle["vignette_future_until"] = (
                        result["valid_until"].date().isoformat()
                        if result.get("valid_until")
                        else ""
                    )
                else:
                    vehicle["vignette_status"] = "missing"
                    vehicle["vignette_until"] = ""
                    vehicle.pop("vignette_future_until", None)
                updated += 1
                changed = True
                print(
                    f"eDalnice refresh OK: {spz} status={vehicle.get('vignette_status')} "
                    f"until={vehicle.get('vignette_until') or vehicle.get('vignette_future_until') or '-'}"
                )
            except Exception as exc:
                error_text = f"{spz}: {type(exc).__name__}: {exc}"
                errors.append(error_text)
                print(f"eDalnice refresh error: {error_text}")
                vehicle["vignette_last_error"] = str(exc)[:300]
                # Neaktualizujeme checked_at a hlavně nemažeme poslední známou platnost.
                changed = True

        if changed:
            core.save_vehicles(
                vehicles,
                f"Cache dálničních známek ({updated} obnoveno, {skipped} z DB)",
            )
        return {"updated": updated, "skipped": skipped, "errors": errors}
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
                print(
                    f"eDalnice refresh batch OK: {result.get('updated', 0)} obnoveno, "
                    f"{result.get('skipped', 0)} z DB"
                )
        except Exception as exc:
            print(f"eDalnice background refresh failed: {exc}")

    threading.Thread(target=worker, name="edalnice-refresh", daemon=True).start()


def register(core):
    app = core.app

    @app.before_request
    def auto_vignette_refresh():
        _maybe_start_vignette_refresh(core)

    @app.route("/admin/vignette-refresh", methods=["POST"])
    def admin_vignette_refresh():
        if not core.require_admin():
            return core.redirect(core.url_for("login"))
        # Ruční tlačítko je jediná cesta, která cache vědomě obejde.
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
