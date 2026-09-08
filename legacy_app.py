from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, session, jsonify
from pathlib import Path
from werkzeug.utils import secure_filename
from datetime import datetime, date, timezone
import hashlib
import hmac
import os
import uuid

import requests
import db_storage

APP_DIR = Path(__file__).resolve().parent
STATIC_IMAGES_DIR = APP_DIR / "static" / "images"
STATIC_DOCS_DIR = APP_DIR / "static" / "documents"
_storage_root_raw = os.environ.get("STORAGE_ROOT", "").strip()
STORAGE_ROOT = Path(_storage_root_raw) if _storage_root_raw else (APP_DIR / "static")
IMAGES_DIR = STORAGE_ROOT / "images"
DOCS_DIR = STORAGE_ROOT / "documents"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

DATOVA_KOSTKA_API_KEY = os.environ.get("DATOVA_KOSTKA_API_KEY", "").strip()
DATOVA_KOSTKA_URL = "https://api.dataovozidlech.cz/api/vehicletechnicaldata/v2"

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
_ADMIN_DEFAULT_SALT = bytes.fromhex("de553a8131e1980b61bc07b48c0ba857")
_ADMIN_DEFAULT_HASH = bytes.fromhex("6f00edbf700f9d148436f040f2b4ef1162acdfbae67432832285836e6eb11cf6")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-crocodille-rent")


def verify_admin_password(password):
    configured = os.environ.get("ADMIN_PASSWORD", "")
    if configured:
        return hmac.compare_digest(str(configured), str(password or ""))
    candidate = hashlib.pbkdf2_hmac(
        "sha256", str(password or "").encode("utf-8"), _ADMIN_DEFAULT_SALT, 600_000
    )
    return hmac.compare_digest(candidate, _ADMIN_DEFAULT_HASH)


def parse_date(value):
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date() if value else None
    except Exception:
        return None


def days_until(value):
    parsed = parse_date(value)
    return (parsed - date.today()).days if parsed else None


def _legacy_insurance_until(vehicle):
    if str(vehicle.get("insurance_until") or "").strip():
        return str(vehicle.get("insurance_until"))[:10]
    candidates = []
    for key in ("liability_until", "casco_until"):
        raw = str(vehicle.get(key) or "").strip()[:10]
        parsed = parse_date(raw)
        if parsed:
            candidates.append((parsed, raw))
    if candidates:
        # Konzervativně bereme dřívější konec, pokud se staré POV/HAV liší.
        return min(candidates, key=lambda item: item[0])[1]
    return ""


def normalize_vehicle(vehicle):
    v = dict(vehicle or {})
    v["insurance_until"] = _legacy_insurance_until(v)
    v.pop("liability_until", None)
    v.pop("casco_until", None)
    v.setdefault("documents", [])
    v.setdefault("service_records", [])
    v.setdefault("assistance_until", "")
    v.setdefault("next_service_km", "")
    v.setdefault("next_service_note", "")
    v.setdefault("photo", "")
    v.setdefault("status", "V provozu")
    return v


def load_vehicles():
    vehicles = db_storage.load_vehicles()
    normalized = [normalize_vehicle(v) for v in vehicles]
    if normalized != vehicles:
        db_storage.save_vehicles(normalized)
    return normalized


def save_vehicles(vehicles, commit_message=""):
    # commit_message zůstává jen kvůli kompatibilitě starších volání.
    db_storage.save_vehicles([normalize_vehicle(v) for v in vehicles])


def get_vehicle(vehicle_id):
    needle = str(vehicle_id or "").strip().upper()
    for vehicle in load_vehicles():
        values = {
            str(vehicle.get("id") or "").strip().upper(),
            str(vehicle.get("spz") or "").strip().upper(),
            str(vehicle.get("vehicle_id") or "").strip().upper(),
        }
        if needle in values:
            return vehicle
    return None


def update_vehicle(vehicle_id, updater, commit_message=None):
    vehicles = load_vehicles()
    for index, vehicle in enumerate(vehicles):
        if str(vehicle.get("id")) == str(vehicle_id):
            vehicles[index] = normalize_vehicle(updater(dict(vehicle)))
            save_vehicles(vehicles, commit_message or f"Upraveno vozidlo {vehicle_id}")
            return vehicles[index]
    return None


def is_active_vehicle(vehicle):
    status = str(vehicle.get("status", "")).strip().lower()
    return not (
        status.startswith("vráceno")
        or status.startswith("vraceno")
        or status in ("mimo provoz", "odstaveno", "odstavené")
    )


def status_for(value):
    parsed = parse_date(value)
    if not parsed:
        return ("unknown", "nezadáno")
    days = (parsed - date.today()).days
    if days < 0:
        return ("bad", "propadlé")
    if days <= 14:
        return ("bad", f"{days} dní")
    if days <= 45:
        return ("soon", f"{days} dní")
    return ("ok", "OK")


def vehicle_alert(vehicle):
    if not is_active_vehicle(vehicle):
        return "inactive"
    states = [
        status_for(vehicle.get(key))[0]
        for key in ("stk_until", "vignette_until", "insurance_until", "assistance_until")
        if vehicle.get(key)
    ]
    return "bad" if "bad" in states else "soon" if "soon" in states else "ok" if "ok" in states else "unknown"


def vehicle_alert_items(vehicle):
    if not is_active_vehicle(vehicle):
        return []
    result = []
    for key, label in (
        ("stk_until", "STK"),
        ("vignette_until", "Dálniční známka"),
        ("insurance_until", "Pojištění"),
        ("assistance_until", "Asistence"),
    ):
        if vehicle.get(key):
            state, text = status_for(vehicle.get(key))
            if state in ("bad", "soon"):
                result.append({"key": key, "label": label, "state": state, "text": text})
    return result


def _flatten_dict(value, out=None):
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                _flatten_dict(item, out)
            elif item not in (None, ""):
                out[str(key).lower()] = item
    elif isinstance(value, list):
        for item in value:
            _flatten_dict(item, out)
    return out


def _flatten_details(value, prefix="", out=None):
    out = [] if out is None else out
    if isinstance(value, dict):
        for key, item in value.items():
            label = f"{prefix} / {key}" if prefix else str(key)
            if isinstance(item, (dict, list)):
                _flatten_details(item, label, out)
            elif item not in (None, ""):
                out.append({"label": label, "value": str(item)})
    elif isinstance(value, list):
        for index, item in enumerate(value, 1):
            _flatten_details(item, f"{prefix} {index}".strip(), out)
    return out


def _pick(flat, *keys):
    for key in keys:
        value = flat.get(key.lower())
        if value not in (None, ""):
            return value
    return ""


def _brand_first(value):
    text = str(value or "").strip().upper()
    if "IVECO" in text:
        return "IVECO"
    if "RENAULT" in text:
        return "RENAULT"
    return text.split("/")[0].split()[0] if text else ""


def _model_name(value, brand=""):
    text = str(value or "").strip().upper()
    brand_text = str(brand or "").upper()
    if "IVECO" in brand_text or "DAILY" in text:
        return "DAILY"
    if "RENAULT" in brand_text or "MASTER" in text:
        return "MASTER"
    return text.split("/")[0] if text else ""


def _fuel_name(value):
    return {
        "NM": "Nafta",
        "BA": "Benzín",
        "EL": "Elektřina",
        "LPG": "LPG",
        "CNG": "CNG",
    }.get(str(value or "").strip().upper(), str(value or "").strip())


def _clean_number(value):
    try:
        number = float(str(value).strip().replace(",", "."))
        return str(int(number)) if number.is_integer() else str(number)
    except Exception:
        return str(value or "").strip()


def _power_kw(value):
    text = str(value or "").strip()
    return _clean_number(text.split("/")[0].strip()) if text else ""


def datova_kostka_basic(data):
    flat = _flatten_dict(data)
    first_registration = _pick(flat, "DatumPrvniRegistrace", "DatumPrvniRegistraceVCR", "PrvniRegistrace")
    year = str(first_registration)[:4] if first_registration else _pick(flat, "RokVyroby", "Rok")
    brand = _brand_first(_pick(flat, "TovarniZnacka", "Znacka", "Vyrobce"))
    model_raw = _pick(flat, "ObchodniOznaceni", "Model")
    return {
        "vin": _pick(flat, "VIN"),
        "spz": _pick(flat, "NovaRegistracniZnacka", "RegistracniZnacka", "RZ", "SPZ"),
        "brand": brand,
        "model": _model_name(model_raw, brand),
        "year": year,
        "first_registration": first_registration,
        "fuel": _fuel_name(_pick(flat, "Palivo", "DruhPaliva")),
        "engine_type": _pick(flat, "MotorTyp", "TypMotoru"),
        "engine_capacity": _clean_number(_pick(flat, "MotorZdvihObjem", "ZdvihovyObjem", "ObjemMotoru")),
        "power_kw": _power_kw(_pick(flat, "MotorMaxVykon", "MaxVykon", "Vykon")),
        "emission": _pick(flat, "EmisniUroven", "EmisniNorma"),
        "inspection_until": _pick(flat, "PravidelnaTechnickaProhlidkaDo", "TechnickaProhlidkaDo"),
    }


def fetch_datova_kostka(vin):
    if not DATOVA_KOSTKA_API_KEY:
        raise RuntimeError("Na Renderu není nastaven DATOVA_KOSTKA_API_KEY.")
    vin = str(vin or "").strip().upper()
    if len(vin) != 17:
        raise ValueError("VIN musí mít 17 znaků.")
    response = requests.get(
        DATOVA_KOSTKA_URL,
        params={"vin": vin},
        headers={"API_KEY": DATOVA_KOSTKA_API_KEY},
        timeout=30,
    )
    if response.status_code in (401, 403):
        raise RuntimeError("Datová kostka odmítla API klíč.")
    response.raise_for_status()
    payload = response.json()
    data = payload.get("Data", payload.get("data", payload)) if isinstance(payload, dict) else payload
    if not data:
        raise RuntimeError("Datová kostka pro tento VIN nevrátila žádná data.")
    return data


def make_datova_kostka(vin, data):
    return {
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vin": str(vin or "").strip().upper(),
        "basic": datova_kostka_basic(data),
        "details": _flatten_details(data),
        "raw": data,
    }


def apply_datova_kostka(vehicle, dk):
    basic = dk.get("basic") or {}
    vehicle["datova_kostka"] = dk
    vehicle["stk_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    vehicle["stk_source"] = "datova_kostka"
    vehicle.pop("stk_last_error", None)
    if basic.get("spz"):
        vehicle["spz"] = str(basic.get("spz") or "").strip().upper()
    for target, source in (
        ("brand", "brand"),
        ("model", "model"),
        ("year", "year"),
        ("fuel", "fuel"),
        ("engine_type", "engine_type"),
        ("engine_capacity", "engine_capacity"),
        ("power_kw", "power_kw"),
        ("emission", "emission"),
        ("first_registration", "first_registration"),
    ):
        if basic.get(source) not in (None, ""):
            vehicle[target] = str(basic.get(source))
    inspection_until = str(basic.get("inspection_until") or "").strip()[:10]
    if inspection_until:
        vehicle["stk_until"] = inspection_until
    return vehicle


def refresh_stk_all():
    vehicles = load_vehicles()
    updated = 0
    errors = []
    changed = False
    for vehicle in vehicles:
        vin = str(vehicle.get("vin") or "").strip().upper()
        if len(vin) != 17 or not is_active_vehicle(vehicle):
            continue
        try:
            dk = make_datova_kostka(vin, fetch_datova_kostka(vin))
            apply_datova_kostka(vehicle, dk)
            updated += 1
            changed = True
        except Exception as exc:
            vehicle["stk_last_error"] = str(exc)[:300]
            errors.append(f"{vin}: {type(exc).__name__}: {exc}")
            changed = True
    if changed:
        save_vehicles(vehicles)
    return {"updated": updated, "errors": errors}


def dashboard_data(vehicles):
    return {
        "total": len(vehicles),
        "active": len([v for v in vehicles if is_active_vehicle(v)]),
    }


@app.template_filter("czdate")
def czdate(value):
    parsed = parse_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else ""


@app.template_filter("czdate_short")
def czdate_short(value):
    parsed = parse_date(value)
    return f"{parsed.day}.{parsed.month}.{parsed.year}" if parsed else ""


@app.template_filter("km")
def km(value):
    try:
        return f"{int(value):,}".replace(",", " ") + " km"
    except Exception:
        return value or ""


def require_admin():
    return session.get("admin") is True


@app.context_processor
def inject_helpers():
    return {
        "status_for": status_for,
        "vehicle_alert": vehicle_alert,
        "vehicle_alert_items": vehicle_alert_items,
        "is_active_vehicle": is_active_vehicle,
        "admin_logged": require_admin(),
    }


@app.route("/")
def index():
    query = request.args.get("q", "").strip().lower()
    vehicles = load_vehicles()
    if query:
        vehicles = [
            v for v in vehicles
            if query in " ".join(
                str(v.get(key, "")) for key in ("spz", "vin", "brand", "model", "name", "vehicle_id")
            ).lower()
        ]
    return render_template("index.html", vehicles=vehicles, q=query)


@app.route("/v/<vehicle_id>")
def vehicle(vehicle_id):
    found = get_vehicle(vehicle_id)
    return render_template("vehicle.html", v=found) if found else (render_template("not_found.html", vehicle_id=vehicle_id), 404)


@app.route("/admin/login", endpoint="login")
def login():
    return redirect(url_for("client_login", next="/admin"))


@app.route("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("client_login"))


@app.route("/admin")
def admin():
    if not require_admin():
        return redirect(url_for("client_login", next="/admin"))
    vehicles = load_vehicles()
    return render_template("admin.html", vehicles=vehicles, dashboard=dashboard_data(vehicles))


@app.route("/admin/add-by-vin", methods=["POST"])
def add_vehicle_by_vin():
    if not require_admin():
        return redirect(url_for("client_login", next="/admin"))
    vin = (request.form.get("vin") or "").strip().upper()
    if len(vin) != 17:
        flash("VIN musí mít 17 znaků.")
        return redirect(url_for("admin"))
    vehicles = load_vehicles()
    for vehicle in vehicles:
        if (vehicle.get("vin") or "").strip().upper() == vin:
            flash("Vozidlo s tímto VIN už v evidenci existuje.")
            return redirect(url_for("edit", vehicle_id=vehicle.get("id")))
    try:
        dk = make_datova_kostka(vin, fetch_datova_kostka(vin))
        basic = dk.get("basic") or {}
        new_id = uuid.uuid4().hex[:10]
        new_vehicle = normalize_vehicle({
            "id": new_id,
            "spz": str(basic.get("spz") or "").strip().upper(),
            "vehicle_id": "",
            "name": basic.get("model") or "Vozidlo",
            "brand": basic.get("brand") or "",
            "model": basic.get("model") or "",
            "vin": vin,
            "year": str(basic.get("year") or ""),
            "status": "V provozu",
            "stk_until": str(basic.get("inspection_until") or "")[:10],
            "vignette_until": "",
            "vignette_status": "unknown",
            "insurance_until": "",
            "assistance_until": "",
            "next_service_km": "",
            "next_service_note": "",
            "note": "",
            "photo": "",
            "documents": [],
            "service_records": [],
        })
        apply_datova_kostka(new_vehicle, dk)
        if new_vehicle.get("spz"):
            try:
                import service_fleet
                service_fleet.apply_vignette_result(new_vehicle, service_fleet.lookup_vignette(new_vehicle["spz"]))
            except Exception as exc:
                new_vehicle["vignette_last_error"] = str(exc)[:300]
        vehicles.append(new_vehicle)
        save_vehicles(vehicles)
        flash("Vozidlo bylo vytvořeno podle VIN, uloženo do PostgreSQL a automaticky ověřeno.")
        return redirect(url_for("edit", vehicle_id=new_id))
    except Exception as exc:
        flash(str(exc))
        return redirect(url_for("admin"))


@app.route("/admin/<vehicle_id>", methods=["GET", "POST"])
def edit(vehicle_id):
    if not require_admin():
        return redirect(url_for("client_login", next=f"/admin/{vehicle_id}"))
    vehicle = get_vehicle(vehicle_id)
    if not vehicle:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404
    if request.method == "POST":
        fields = [
            "spz", "vehicle_id", "brand", "name", "model", "vin", "year", "km", "status",
            "insurance_until", "assistance_until", "next_service_km", "next_service_note", "note",
        ]

        def updater(item):
            for field in fields:
                if field in request.form:
                    item[field] = (request.form.get(field) or "").strip()
            photo = request.files.get("photo")
            if photo and photo.filename:
                ext = Path(photo.filename).suffix.lower()
                if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                    raise ValueError("Fotografie musí být JPG, PNG nebo WEBP.")
                filename = secure_filename(f"{item.get('id')}_{uuid.uuid4().hex[:8]}{ext}")
                photo.save(IMAGES_DIR / filename)
                old_photo = str(item.get("photo") or "")
                item["photo"] = filename
                if old_photo:
                    _delete_physical_file(old_photo, IMAGES_DIR, STATIC_IMAGES_DIR)
            return item

        try:
            update_vehicle(vehicle["id"], updater)
            flash("Uloženo do PostgreSQL.")
        except Exception as exc:
            flash(str(exc))
        return redirect(url_for("edit", vehicle_id=vehicle["id"]))
    return render_template("edit.html", v=vehicle)


@app.route("/admin/<vehicle_id>/datova-kostka", methods=["POST"])
def load_datova_kostka(vehicle_id):
    if not require_admin():
        return redirect(url_for("client_login", next=f"/admin/{vehicle_id}"))
    vehicle = get_vehicle(vehicle_id)
    if not vehicle:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404
    try:
        dk = make_datova_kostka(vehicle.get("vin"), fetch_datova_kostka(vehicle.get("vin")))
        update_vehicle(vehicle["id"], lambda item: apply_datova_kostka(item, dk))
        flash("Údaje z Datové kostky byly obnoveny a uloženy do PostgreSQL.")
    except Exception as exc:
        flash(str(exc))
    return redirect(url_for("vehicle", vehicle_id=vehicle["id"]))


def _delete_physical_file(filename, *directories):
    safe = Path(str(filename or "")).name
    if not safe:
        return
    for directory in directories:
        try:
            path = Path(directory) / safe
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass


@app.route("/admin/<vehicle_id>/documents/add", methods=["POST"])
def add_document(vehicle_id):
    if not require_admin():
        return redirect(url_for("client_login", next=f"/admin/{vehicle_id}"))
    vehicle = get_vehicle(vehicle_id)
    if not vehicle:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404
    uploaded = request.files.get("document")
    if not uploaded or not uploaded.filename:
        flash("Soubor nebyl vybrán.")
        return redirect(url_for("edit", vehicle_id=vehicle["id"]))
    original_name = Path(uploaded.filename).name
    ext = Path(original_name).suffix.lower()
    if ext != ".pdf":
        flash("Dokument musí být ve formátu PDF.")
        return redirect(url_for("edit", vehicle_id=vehicle["id"]))
    safe_original = secure_filename(original_name) or f"dokument{ext}"
    filename = secure_filename(f"{vehicle['id']}_{uuid.uuid4().hex[:8]}_{safe_original}")
    uploaded.save(DOCS_DIR / filename)

    def updater(item):
        item.setdefault("documents", []).append({
            "id": uuid.uuid4().hex[:10],
            "title": original_name,
            "filename": filename,
            "original_name": original_name,
            "type": "PDF",
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        })
        return item

    update_vehicle(vehicle["id"], updater)
    flash("Dokument byl uložen.")
    return redirect(url_for("edit", vehicle_id=vehicle["id"]))


@app.route("/admin/<vehicle_id>/documents/<doc_id>/delete", methods=["POST"])
def delete_document(vehicle_id, doc_id):
    if not require_admin():
        return redirect(url_for("client_login", next=f"/admin/{vehicle_id}"))
    vehicle = get_vehicle(vehicle_id)
    if not vehicle:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404
    target = next((doc for doc in vehicle.get("documents", []) if str(doc.get("id")) == str(doc_id)), None)
    if target:
        _delete_physical_file(target.get("filename"), DOCS_DIR, STATIC_DOCS_DIR)

    def updater(item):
        item["documents"] = [doc for doc in item.get("documents", []) if str(doc.get("id")) != str(doc_id)]
        return item

    update_vehicle(vehicle["id"], updater)
    flash("Dokument byl odstraněn z databáze i fyzického úložiště.")
    return redirect(url_for("edit", vehicle_id=vehicle["id"]))


@app.route("/admin/<vehicle_id>/service/add", methods=["POST"])
def add_service(vehicle_id):
    if not require_admin():
        return redirect(url_for("client_login", next=f"/admin/{vehicle_id}"))
    vehicle = get_vehicle(vehicle_id)
    if not vehicle:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404
    record = {
        "id": uuid.uuid4().hex[:10],
        "date": request.form.get("date", ""),
        "km": request.form.get("km", ""),
        "title": request.form.get("title", ""),
        "next_service": request.form.get("next_service", ""),
    }

    def updater(item):
        item.setdefault("service_records", []).append(record)
        return item

    update_vehicle(vehicle["id"], updater)
    flash("Servisní záznam přidán.")
    return redirect(url_for("edit", vehicle_id=vehicle["id"]))


@app.route("/admin/<vehicle_id>/service/<sid>/delete", methods=["POST"])
def delete_service(vehicle_id, sid):
    if not require_admin():
        return redirect(url_for("client_login", next=f"/admin/{vehicle_id}"))
    vehicle = get_vehicle(vehicle_id)
    if not vehicle:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404

    def updater(item):
        item["service_records"] = [record for record in item.get("service_records", []) if str(record.get("id")) != str(sid)]
        return item

    update_vehicle(vehicle["id"], updater)
    flash("Servisní záznam smazán.")
    return redirect(url_for("edit", vehicle_id=vehicle["id"]))


@app.route("/admin/<vehicle_id>/delete", methods=["POST"])
def delete_vehicle(vehicle_id):
    if not require_admin():
        return redirect(url_for("client_login", next="/admin"))
    vehicles = load_vehicles()
    target = next((v for v in vehicles if str(v.get("id")) == str(vehicle_id)), None)
    if not target:
        flash("Vozidlo nebylo nalezeno.")
        return redirect(url_for("admin"))
    if target.get("photo"):
        _delete_physical_file(target.get("photo"), IMAGES_DIR, STATIC_IMAGES_DIR)
    for doc in target.get("documents", []):
        _delete_physical_file(doc.get("filename"), DOCS_DIR, STATIC_DOCS_DIR)
    vehicles = [v for v in vehicles if str(v.get("id")) != str(vehicle_id)]
    save_vehicles(vehicles)
    flash(f"Vozidlo {target.get('spz') or vehicle_id} bylo smazáno.")
    return redirect(url_for("admin"))


@app.route("/documents/<path:filename>")
def documents(filename):
    if not (require_admin() or session.get("client") is True):
        return redirect(url_for("client_login", next=request.path))
    safe = Path(filename).name
    if (DOCS_DIR / safe).exists():
        return send_from_directory(DOCS_DIR, safe, as_attachment=False)
    return send_from_directory(STATIC_DOCS_DIR, safe, as_attachment=False)


@app.route("/vehicle-images/<path:filename>")
def vehicle_image(filename):
    safe = Path(filename).name
    if (IMAGES_DIR / safe).exists():
        return send_from_directory(IMAGES_DIR, safe)
    return send_from_directory(STATIC_IMAGES_DIR, safe)


@app.route("/qr")
def qr():
    if not require_admin():
        return redirect(url_for("client_login", next="/qr"))
    return render_template("qr.html", vehicles=load_vehicles(), base=request.url_root.rstrip("/"))


def _api_alerts_for_vehicle(vehicle, horizon=30):
    alerts = []
    if not is_active_vehicle(vehicle):
        return alerts
    for key, label in (
        ("stk_until", "STK"),
        ("vignette_until", "Dálniční známka"),
        ("insurance_until", "Pojištění"),
        ("assistance_until", "Asistence"),
    ):
        value = vehicle.get(key)
        days = days_until(value)
        if days is None or days > horizon:
            continue
        severity = "overdue" if days < 0 else "critical" if days <= 7 else "warning" if days <= 14 else "info"
        text = (
            f"{label} je po termínu {abs(days)} dní" if days < 0
            else f"{label} končí dnes" if days == 0
            else f"{label} končí za {days} dní"
        )
        alerts.append({
            "id": f"{vehicle.get('id')}:{key}:{value}",
            "vehicle_id": str(vehicle.get("id") or ""),
            "spz": vehicle.get("spz") or "",
            "kind": key,
            "title": label,
            "message": text,
            "date": str(value or "")[:10],
            "days": days,
            "severity": severity,
        })
    return alerts


def _api_text(value):
    return "" if value is None else str(value)


def _photo_url(vehicle):
    if not vehicle.get("photo"):
        return ""
    return url_for("vehicle_image", filename=vehicle.get("photo"), _external=True)


def _api_vehicle(vehicle):
    dk = vehicle.get("datova_kostka") or {}
    basic = dk.get("basic") or {}
    return {
        "id": _api_text(vehicle.get("id")),
        "spz": _api_text(vehicle.get("spz")),
        "vehicle_number": _api_text(vehicle.get("vehicle_id")),
        "vin": _api_text(vehicle.get("vin")),
        "brand": _api_text(basic.get("brand") or vehicle.get("brand")),
        "model": _api_text(basic.get("model") or vehicle.get("model") or vehicle.get("name")),
        "year": _api_text(basic.get("year") or vehicle.get("year")),
        "status": _api_text(vehicle.get("status") or "V provozu"),
        "km": _api_text(vehicle.get("km")),
        "stk_until": _api_text(vehicle.get("stk_until") or basic.get("inspection_until")),
        "vignette_until": _api_text(vehicle.get("vignette_until")),
        "vignette_status": _api_text(vehicle.get("vignette_status")),
        "vignette_future_from": _api_text(vehicle.get("vignette_future_from")),
        "vignette_future_until": _api_text(vehicle.get("vignette_future_until")),
        "insurance_until": _api_text(vehicle.get("insurance_until")),
        "assistance_until": _api_text(vehicle.get("assistance_until")),
        "next_service_km": _api_text(vehicle.get("next_service_km")),
        "fuel": _api_text(basic.get("fuel") or vehicle.get("fuel")).upper(),
        "engine_type": _api_text(basic.get("engine_type") or vehicle.get("engine_type")),
        "engine_capacity": _api_text(basic.get("engine_capacity") or vehicle.get("engine_capacity")),
        "power_kw": _api_text(basic.get("power_kw") or vehicle.get("power_kw")),
        "emission": _api_text(basic.get("emission") or vehicle.get("emission")),
        "first_registration": _api_text(basic.get("first_registration") or vehicle.get("first_registration")),
        "photo_url": _photo_url(vehicle),
        "alerts": _api_alerts_for_vehicle(vehicle),
    }


@app.route("/api/v1/health")
def api_health():
    try:
        count = db_storage.count_vehicles()
        return jsonify({
            "ok": True,
            "service": "crocodille-fleet",
            "version": 2,
            "persistence": "postgresql",
            "vehicle_count": count,
            "datova_kostka_configured": bool(DATOVA_KOSTKA_API_KEY),
            "daily_sync": db_storage.latest_job("daily-sync"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "persistence": "postgresql"}), 500


@app.route("/api/v1/vehicles")
def api_vehicles():
    return jsonify({"vehicles": [_api_vehicle(v) for v in load_vehicles()]})


@app.route("/api/v1/vehicles/<vehicle_id>")
def api_vehicle_detail(vehicle_id):
    vehicle = get_vehicle(vehicle_id)
    return jsonify(_api_vehicle(vehicle)) if vehicle else (jsonify({"error": "Vozidlo nenalezeno"}), 404)


@app.route("/api/v1/alerts")
def api_alerts():
    try:
        horizon = max(1, min(int(request.args.get("days", "30")), 365))
    except Exception:
        horizon = 30
    alerts = []
    for vehicle in load_vehicles():
        alerts.extend(_api_alerts_for_vehicle(vehicle, horizon))
    alerts.sort(key=lambda item: item["days"])
    return jsonify({"days": horizon, "count": len(alerts), "alerts": alerts})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
