from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, session
from pathlib import Path
from werkzeug.utils import secure_filename
from datetime import datetime, date
import json, os, uuid
import requests

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "vehicles.json"
IMAGES_DIR = APP_DIR / "static" / "images"
DOCS_DIR = APP_DIR / "static" / "documents"

ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")
MD_API_KEY = os.environ.get("MD_API_KEY", "mTcto2N0E3XEto6jnFEp370s8nKbPto3")
MD_API_URL = "https://api.dataovozidlech.cz/api/vehicletechnicaldata/v2"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-crocodille-rent")

IMAGES_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)


def load_vehicles():
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_vehicles(vehicles):
    DATA_FILE.write_text(json.dumps(vehicles, ensure_ascii=False, indent=2), encoding="utf-8")


def get_vehicle(vehicle_id):
    for v in load_vehicles():
        if str(v.get("id")) == str(vehicle_id):
            return v
    return None


def update_vehicle(vehicle_id, updater):
    vehicles = load_vehicles()
    for i, v in enumerate(vehicles):
        if str(v.get("id")) == str(vehicle_id):
            vehicles[i] = updater(v)
            save_vehicles(vehicles)
            return vehicles[i]
    return None


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def status_for(value):
    d = parse_date(value)
    if not d:
        return ("unknown", "nezadáno")
    days = (d - date.today()).days
    if days < 0:
        return ("bad", "propadlé")
    if days <= 14:
        return ("bad", f"{days} dní")
    if days <= 45:
        return ("soon", f"{days} dní")
    return ("ok", "OK")


def vehicle_alert(v):
    keys = [
        "stk_until",
        "vignette_until",
        "liability_until",
        "casco_until",
        "assistance_until",
        "next_service_date",
    ]
    classes = [status_for(v.get(k))[0] for k in keys if v.get(k)]
    if "bad" in classes:
        return "bad"
    if "soon" in classes:
        return "soon"
    if "ok" in classes:
        return "ok"
    return "unknown"


def vehicle_alert_items(v):
    items = [
        ("stk_until", "STK"),
        ("vignette_until", "Dálniční známka"),
        ("liability_until", "Povinné ručení"),
        ("casco_until", "Havarijní pojištění"),
        ("assistance_until", "Asistence"),
        ("next_service_date", "Příští servis"),
    ]

    result = []
    for key, label in items:
        value = v.get(key)
        if not value:
            continue

        state, text = status_for(value)

        if state in ("bad", "soon"):
            result.append({
                "key": key,
                "label": label,
                "state": state,
                "text": text,
            })

    return result


@app.template_filter("czdate")
def czdate(value):
    d = parse_date(value)
    return d.strftime("%d.%m.%Y") if d else ""


@app.template_filter("km")
def km(value):
    try:
        return f"{int(value):,}".replace(",", " ") + " km"
    except Exception:
        return value or ""


@app.context_processor
def inject_helpers():
    return dict(
        status_for=status_for,
        vehicle_alert=vehicle_alert,
        vehicle_alert_items=vehicle_alert_items,
        admin_logged=session.get("admin") is True,
    )


def require_admin():
    return session.get("admin") is True


@app.route("/")
def index():
    q = request.args.get("q", "").strip().lower()
    vehicles = load_vehicles()

    if q:
        vehicles = [
            v for v in vehicles
            if q in " ".join(str(v.get(k, "")) for k in ["spz", "vin", "brand", "name", "vehicle_id"]).lower()
        ]

    return render_template("index.html", vehicles=vehicles, q=q)


@app.route("/v/<vehicle_id>")
def vehicle(vehicle_id):
    v = get_vehicle(vehicle_id)
    if not v:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404
    return render_template("vehicle.html", v=v)


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("pin") == ADMIN_PIN:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Špatný PIN.")
    return render_template("login.html")


@app.route("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin")
def admin():
    if not require_admin():
        return redirect(url_for("login"))
    return render_template("admin.html", vehicles=load_vehicles())


@app.route("/admin/<vehicle_id>", methods=["GET", "POST"])
def edit(vehicle_id):
    if not require_admin():
        return redirect(url_for("login"))

    v = get_vehicle(vehicle_id)
    if not v:
        return render_template("not_found.html", vehicle_id=vehicle_id), 404

    if request.method == "POST":
        fields = [
            "spz", "vehicle_id", "brand", "name", "model", "vin", "year", "km",
            "status", "stk_until", "vignette_until", "liability_until",
            "casco_until", "assistance_until", "next_service_date",
            "next_service_km", "next_service_note", "note"
        ]

        def updater(x):
            for f in fields:
                if f in request.form:
                    value = request.form.get(f)
                    if value is not None:
                        x[f] = value.strip()

            photo = request.files.get("photo")
            if photo and photo.filename:
                ext = Path(photo.filename).suffix.lower()
                fname = secure_filename(f"{x.get('id')}_{uuid.uuid4().hex[:8]}{ext}")
                photo.save(IMAGES_DIR / fname)
                x["photo"] = fname

            return x

        update_vehicle(vehicle_id, updater)
        flash("Uloženo.")
        return redirect(url_for("edit", vehicle_id=vehicle_id))

    return render_template("edit.html", v=v)


@app.route("/admin/<vehicle_id>/documents/add", methods=["POST"])
def add_document(vehicle_id):
    if not require_admin():
        return redirect(url_for("login"))

    f = request.files.get("document")
    title = request.form.get("title", "Dokument").strip()

    if not f or not f.filename:
        flash("Soubor nebyl vybrán.")
        return redirect(url_for("edit", vehicle_id=vehicle_id))

    ext = Path(f.filename).suffix.lower()
    safe_title = secure_filename(title) or "document"
    fname = secure_filename(f"{vehicle_id}_{uuid.uuid4().hex[:8]}_{safe_title}{ext}")
    f.save(DOCS_DIR / fname)

    def updater(v):
        v.setdefault("documents", []).append({
            "id": uuid.uuid4().hex[:10],
            "title": title,
            "filename": fname,
            "original_name": f.filename,
            "type": ext.replace(".", "").upper(),
            "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        return v

    update_vehicle(vehicle_id, updater)
    flash("Dokument nahrán.")
    return redirect(url_for("edit", vehicle_id=vehicle_id))


@app.route("/admin/<vehicle_id>/documents/<doc_id>/delete", methods=["POST"])
def delete_document(vehicle_id, doc_id):
    if not require_admin():
        return redirect(url_for("login"))

    def updater(v):
        docs = []
        for d in v.get("documents", []):
            if d.get("id") == doc_id:
                fn = d.get("filename")
                if fn and (DOCS_DIR / fn).exists():
                    (DOCS_DIR / fn).unlink()
            else:
                docs.append(d)
        v["documents"] = docs
        return v

    update_vehicle(vehicle_id, updater)
    flash("Dokument smazán.")
    return redirect(url_for("edit", vehicle_id=vehicle_id))


@app.route("/admin/<vehicle_id>/service/add", methods=["POST"])
def add_service(vehicle_id):
    if not require_admin():
        return redirect(url_for("login"))

    rec = {
        "id": uuid.uuid4().hex[:10],
        "date": request.form.get("date", ""),
        "km": request.form.get("km", ""),
        "title": request.form.get("title", ""),
        "next_service": request.form.get("next_service", ""),
    }

    def updater(v):
        v.setdefault("service_records", []).append(rec)
        return v

    update_vehicle(vehicle_id, updater)
    flash("Servisní záznam přidán.")
    return redirect(url_for("edit", vehicle_id=vehicle_id))


@app.route("/admin/<vehicle_id>/service/<sid>/delete", methods=["POST"])
def delete_service(vehicle_id, sid):
    if not require_admin():
        return redirect(url_for("login"))

    def updater(v):
        v["service_records"] = [
            s for s in v.get("service_records", [])
            if s.get("id") != sid
        ]
        return v

    update_vehicle(vehicle_id, updater)
    flash("Servisní záznam smazán.")
    return redirect(url_for("edit", vehicle_id=vehicle_id))


@app.route("/documents/<filename>")
def documents(filename):
    return send_from_directory(DOCS_DIR, filename)


@app.route("/qr")
def qr():
    base = request.url_root.rstrip("/")
    return render_template("qr.html", vehicles=load_vehicles(), base=base)


@app.route("/admin/vin/<vehicle_id>")
def load_vin(vehicle_id):
    if not require_admin():
        return {"ok": False, "error": "Nepřihlášený admin"}, 403

    vehicle = get_vehicle(vehicle_id)
    if not vehicle:
        return {"ok": False, "error": "Vozidlo nenalezeno"}, 404

    vin = str(vehicle.get("vin", "")).strip().upper()
    if not vin:
        return {"ok": False, "error": "VIN není zadán"}, 400

    try:
        r = requests.get(
            MD_API_URL,
            headers={"API_KEY": MD_API_KEY},
            params={"vin": vin},
            timeout=20
        )
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"Chyba API: {e}"}, 500

    if data.get("Status") != 1 or not data.get("Data"):
        return {
            "ok": False,
            "error": "VIN nebyl nalezen v Datové kostce",
            "status": data.get("Status")
        }, 404

    d = data["Data"]

    def updater(v):
        if not v.get("brand"):
            v["brand"] = d.get("TovarniZnacka", "")
        if not v.get("model"):
            v["model"] = d.get("ObchodniOznaceni", "")

        first_reg = d.get("DatumPrvniRegistrace")
        if first_reg:
            v["year"] = str(first_reg)[:4]

        stk = d.get("PravidelnaTechnickaProhlidkaDo")
        if stk:
            v["stk_until"] = str(stk)[:10]

        v["fuel"] = d.get("Palivo") or v.get("fuel", "")
        v["color"] = d.get("VozidloKaroserieBarva") or v.get("color", "")

        # Technická data z Datové kostky
        v["vehicle_kind"] = d.get("VozidloDruh", "")
        v["body_type"] = d.get("VozidloDruh2", "")
        v["category"] = d.get("Kategorie", "")
        v["engine_type"] = d.get("MotorTyp", "")
        v["engine_power"] = d.get("MotorMaxVykon", "")
        v["engine_volume"] = d.get("MotorZdvihObjem", "")
        v["emission_class"] = d.get("EmisniUroven", "")
        v["dimensions"] = d.get("Rozmery", "")
        v["wheelbase"] = d.get("RozmeryRozvor", "")
        v["weight_running"] = d.get("HmotnostiProvozni", "")
        v["weight_max"] = d.get("HmotnostiPripPov", "")
        v["orv"] = d.get("CisloOrv", "")
        v["rsv_status"] = d.get("StatusNazev", "")

        return v

    update_vehicle(vehicle_id, updater)

    return {
        "ok": True,
        "message": "Data načtena z Datové kostky"
    }


if __name__ == "__main__":
    app.run(debug=True)

