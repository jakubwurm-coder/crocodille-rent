import io

import qrcode
import qrcode.image.svg
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

import legacy_app as core
from service_fleet import register

register(core)
app = core.app

VANS_CENTRE_LOGO_URL = "https://img.classistatic.de/api/v1/mo-prod/images/67/671ecf7e-9971-4a73-928f-537b147fa761?rule=mo-640.jpg"


def _mobile_vehicle(vehicle):
    data = core._api_vehicle(vehicle)
    if not data.get("photo_url"):
        label = " ".join(
            str(vehicle.get(key) or "")
            for key in ("brand", "model", "name", "type")
        ).upper()
        if "IVECO" in label or "DAILY" in label:
            data["photo_url"] = core.url_for("static", filename="images/iveco_daily.png", _external=True)
        elif "RENAULT" in label or "MASTER" in label:
            data["photo_url"] = core.url_for("static", filename="images/renault_master.png", _external=True)
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
    return f"https://vansrenting-crocodille.onrender.com/v/{public_id}"


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(core.os.environ.get("PORT", 5000)), debug=False)
