import io

import qrcode
import qrcode.image.svg

import legacy_app as core
from service_fleet import register

register(core)
app = core.app


@app.route("/api/vehicles", endpoint="api_vehicles_compat")
def api_vehicles_compat():
    return core.jsonify(core.load_vehicles())


def _vehicle_by_spz(spz):
    wanted = (spz or "").strip().upper()
    for vehicle in core.load_vehicles():
        if str(vehicle.get("spz") or "").strip().upper() == wanted:
            return vehicle
    return None


def _public_vehicle_url(vehicle):
    public_id = str(vehicle.get("id") or vehicle.get("spz") or "").strip()
    return f"https://vansrenting-crocodille.onrender.com/v/{public_id}"


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

    download = str(core.request.args.get("download", "")).lower() in {"1", "true", "yes"}
    filename = f"QR_{str(vehicle.get('spz') or 'vozidlo').strip().upper()}.svg"
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return app.response_class(svg, mimetype="image/svg+xml", headers=headers)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(core.os.environ.get("PORT", 5000)), debug=False)
