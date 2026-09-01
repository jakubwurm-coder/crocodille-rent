import hmac
import io
import os

import qrcode
import qrcode.image.svg
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from flask import request, session

import legacy_app as core
from service_fleet import register

register(core)
app = core.app

VANS_CENTRE_LOGO_URL = "https://img.classistatic.de/api/v1/mo-prod/images/67/671ecf7e-9971-4a73-928f-537b147fa761?rule=mo-640.jpg"
PUBLIC_BASE_URL = "https://vansrenting-crocodille.onrender.com"
CLIENT_USERNAME = os.environ.get("CLIENT_USERNAME", "crocodille").strip() or "crocodille"
CLIENT_PASSWORD = os.environ.get("CLIENT_PASSWORD", "Crocodille2026!").strip() or "Crocodille2026!"


def _safe_next_url(value):
    value = str(value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def _client_logged():
    return session.get("client") is True


def _client_route_allowed(path):
    if path == "/":
        return True
    if path.startswith("/v/"):
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/qr-code/"):
        return True
    return False


@app.context_processor
def inject_client_auth():
    return {
        "client_logged": _client_logged(),
        "client_username": session.get("client_username", ""),
    }


@app.before_request
def protect_client_area():
    path = request.path or "/"

    # Veřejně musí zůstat pouze přihlášení, health-check a mobilní API.
    if path in ("/login", "/logout", "/admin/login", "/admin/logout", "/healthz"):
        return None
    if path.startswith("/api/"):
        return None

    # Admin má plný přístup jako doposud.
    if core.require_admin():
        return None

    # Administrační cesty si dál hlídá stávající admin přihlášení.
    if path.startswith("/admin"):
        return None

    # Klient má pouze read-only část s 25 vozidly a jejich podklady.
    if _client_logged():
        if _client_route_allowed(path):
            return None
        return core.app.response_class("Nemáte oprávnění k této části webu.", status=403, mimetype="text/plain")

    # Statické CSS/obrázky přihlašovací stránky musí být dostupné,
    # dokumenty ale bez přihlášení veřejně nevystavujeme.
    if path.startswith("/static/") and not path.startswith("/static/documents/"):
        return None

    next_url = path
    if request.query_string:
        next_url += "?" + request.query_string.decode("utf-8", errors="ignore")
    return core.redirect(core.url_for("client_login", next=_safe_next_url(next_url)))


@app.route("/login", methods=["GET", "POST"], endpoint="client_login")
def client_login():
    if core.require_admin() or _client_logged():
        return core.redirect(core.url_for("index"))

    next_url = _safe_next_url(request.values.get("next"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not CLIENT_PASSWORD:
            core.flash("Klientské heslo zatím není nastavené na serveru.")
        elif hmac.compare_digest(username, CLIENT_USERNAME) and hmac.compare_digest(password, CLIENT_PASSWORD):
            session["client"] = True
            session["client_username"] = CLIENT_USERNAME
            return core.redirect(next_url)
        else:
            core.flash("Nesprávné přihlašovací údaje.")

    return core.render_template("client_login.html", next_url=next_url)


@app.route("/logout", endpoint="client_logout")
def client_logout():
    session.pop("client", None)
    session.pop("client_username", None)
    return core.redirect(core.url_for("client_login"))


def _mobile_vehicle(vehicle):
    data = core._api_vehicle(vehicle)
    if not data.get("photo_url"):
        label = " ".join(
            str(vehicle.get(key) or "")
            for key in ("brand", "model", "name", "type")
        ).upper()
        if "IVECO" in label or "DAILY" in label:
            data["photo_url"] = f"{PUBLIC_BASE_URL}/static/images/iveco_daily.png"
        elif "RENAULT" in label or "MASTER" in label:
            data["photo_url"] = f"{PUBLIC_BASE_URL}/static/images/renault_master.png"
    return data


@app.route("/api/vehicles", endpoint="api_vehicles_compat")
def api_vehicles_compat():
    # Backward-compatible endpoint for older iOS builds. Return the same
    # top-level JSON array they originally decoded, but only with the
    # sanitized mobile fields (no documents, service history or raw data).
    return core.jsonify([_mobile_vehicle(vehicle) for vehicle in core.load_vehicles()])


def _vehicle_by_spz(spz):
    wanted = (spz or "").strip().upper()
    for vehicle in core.load_vehicles():
        if str(vehicle.get("spz") or "").strip().upper() == wanted:
            return vehicle
    return None


def _public_vehicle_url(vehicle):
    public_id = str(vehicle.get("id") or vehicle.get("spz") or "").strip()
    return f"{PUBLIC_BASE_URL}/v/{public_id}"


def _vehicle_label(vehicle):
    brand = str(vehicle.get("brand") or "").strip()
    model = str(vehicle.get("model") or vehicle.get("name") or vehicle.get("type") or "").strip()
    if brand and model and model.lower() not in brand.lower():
        return f"{brand} {model}".strip()
    return brand or model or "Vozidlo"


def _font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _make_qr_pil(target_url, size=360):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,
        border=4,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return image.resize((size, size), Image.Resampling.NEAREST)


def _load_vans_centre_logo():
    try:
        response = requests.get(VANS_CENTRE_LOGO_URL, timeout=10)
        response.raise_for_status()
        logo = Image.open(io.BytesIO(response.content)).convert("RGB")
        return ImageOps.contain(logo, (210, 130), Image.Resampling.LANCZOS)
    except Exception as exc:
        print(f"Vans Centre logo load failed: {exc}")
        return None


def _make_vehicle_qr_card(vehicle):
    width, height = 1200, 760
    navy = "#082F5F"
    blue = "#0B65BD"
    grey = "#536276"
    border = "#DCE4EE"

    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((2, 2, width - 3, height - 3), radius=44, outline=border, width=4, fill="white")

    target_url = _public_vehicle_url(vehicle)
    qr = _make_qr_pil(target_url, 380)

    qr_box = (65, 155, 485, 575)
    draw.rounded_rectangle(qr_box, radius=34, fill="white", outline=border, width=4)
    canvas.paste(qr, (85, 175))

    logo = _load_vans_centre_logo()
    if logo:
        canvas.paste(logo, (630, 55))

    draw.text((855, 72), "Vans", fill=blue, font=_font(32, True))
    draw.text((855, 110), "Centre", fill=blue, font=_font(32, True))
    draw.text((855, 148), "s.r.o.", fill=blue, font=_font(32, True))

    spz = str(vehicle.get("spz") or "").strip().upper()
    label = _vehicle_label(vehicle)
    vehicle_id = str(vehicle.get("vehicle_id") or "").strip()

    draw.text((535, 250), spz, fill=navy, font=_font(72, True))
    draw.text((535, 345), label, fill=grey, font=_font(38, True))
    if vehicle_id:
        draw.text((535, 405), f"ID: {vehicle_id}", fill=navy, font=_font(34, True))

    draw.multiline_text(
        (535, 500),
        "Naskenujte QR kód\npro otevření karty\nvozidla",
        fill=grey,
        font=_font(31, False),
        spacing=8,
    )

    return canvas


@app.route("/qr-code/<spz>.svg", endpoint="vehicle_qr_svg")
def vehicle_qr_svg(spz):
    vehicle = _vehicle_by_spz(spz)
    if not vehicle:
        return core.jsonify({"error": "Vozidlo nenalezeno"}), 404

    target_url = _public_vehicle_url(vehicle)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,
        border=4,
    )
    qr.add_data(target_url)
    qr.make(fit=True)

    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    svg = output.getvalue()

    return app.response_class(svg, mimetype="image/svg+xml")


@app.route("/qr-card/<spz>.png", endpoint="vehicle_qr_card_png")
def vehicle_qr_card_png(spz):
    vehicle = _vehicle_by_spz(spz)
    if not vehicle:
        return core.jsonify({"error": "Vozidlo nenalezeno"}), 404

    card = _make_vehicle_qr_card(vehicle)
    output = io.BytesIO()
    card.save(output, format="PNG", optimize=True)
    png = output.getvalue()

    filename = f"QR_KARTA_{str(vehicle.get('spz') or 'vozidlo').strip().upper()}.png"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return app.response_class(png, mimetype="image/png", headers=headers)


@app.route("/admin/<vehicle_id>/delete", methods=["POST"], endpoint="delete_vehicle")
def delete_vehicle(vehicle_id):
    if not core.require_admin():
        return core.redirect(core.url_for("login"))

    vehicles = core.load_vehicles()
    vehicle = next(
        (item for item in vehicles if str(item.get("id")) == str(vehicle_id)),
        None,
    )
    if not vehicle:
        core.flash("Vozidlo už neexistuje nebo bylo mezitím smazáno.")
        return core.redirect(core.url_for("admin"))

    remaining = [
        item for item in vehicles if str(item.get("id")) != str(vehicle_id)
    ]
    label = str(vehicle.get("spz") or vehicle.get("vehicle_id") or vehicle_id)

    try:
        core.save_vehicles(remaining, f"Smazáno vozidlo {label}")
    except Exception as exc:
        app.logger.exception("Vehicle delete failed")
        core.flash(f"Vozidlo se nepodařilo smazat: {exc}")
        return core.redirect(core.url_for("admin"))

    core.flash(f"Vozidlo {label} bylo trvale smazáno.")
    return core.redirect(core.url_for("admin"))


@app.route("/healthz", endpoint="healthz")
def healthz():
    """Production health check used by Render and external monitoring."""
    try:
        vehicles = core.load_vehicles()
        return core.jsonify({
            "status": "ok",
            "database": "postgresql" if core.db_storage.enabled() else "fallback",
            "vehicle_count": len(vehicles),
        }), 200
    except Exception as exc:
        app.logger.exception("Health check failed")
        return core.jsonify({"status": "error", "error": type(exc).__name__}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(core.os.environ.get("PORT", 5000)), debug=False)
