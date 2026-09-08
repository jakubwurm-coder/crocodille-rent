import hmac
import io
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import qrcode
import qrcode.image.svg
from flask import request, session

import db_storage
import legacy_app as core
import push_notifications
import service_fleet

app = core.app
push_notifications.register_routes(app)

PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://vansrenting-crocodille.onrender.com"
).rstrip("/")
CLIENT_USERNAME = os.environ.get("CLIENT_USERNAME", "crocodille").strip() or "crocodille"
CLIENT_PASSWORD = os.environ.get("CLIENT_PASSWORD", "").strip()
LOCAL_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Europe/Prague"))
BACKGROUND_JOBS_ENABLED = os.environ.get("ENABLE_BACKGROUND_JOBS", "1").strip().lower() not in ("0", "false", "no")


def _safe_next_url(value):
    value = str(value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def _client_logged():
    return session.get("client") is True


@app.context_processor
def inject_auth_context():
    return {
        "client_logged": _client_logged(),
        "client_username": session.get("client_username", ""),
    }


@app.route("/login", methods=["GET", "POST"], endpoint="client_login")
def client_login():
    if core.require_admin():
        return core.redirect(core.url_for("admin"))
    if _client_logged():
        return core.redirect(core.url_for("index"))

    next_url = _safe_next_url(request.values.get("next"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if hmac.compare_digest(username, core.ADMIN_USERNAME) and core.verify_admin_password(password):
            session.clear()
            session["admin"] = True
            session["username"] = core.ADMIN_USERNAME
            return core.redirect(core.url_for("admin"))

        if CLIENT_PASSWORD and hmac.compare_digest(username, CLIENT_USERNAME) and hmac.compare_digest(password, CLIENT_PASSWORD):
            session.clear()
            session["client"] = True
            session["client_username"] = CLIENT_USERNAME
            return core.redirect(next_url)

        core.flash("Nesprávné uživatelské jméno nebo heslo.")

    return core.render_template("client_login.html", next_url=next_url)


@app.route("/logout", endpoint="client_logout")
def client_logout():
    session.clear()
    return core.redirect(core.url_for("client_login"))


@app.before_request
def protect_access():
    path = request.path or "/"

    if path in ("/login", "/logout", "/healthz", "/api/v1/health"):
        return None
    if path.startswith("/api/"):
        return None
    if path.startswith("/v/"):
        return None
    if path.startswith("/vehicle-images/"):
        return None
    if path.startswith("/qr-code/"):
        return None
    if path.startswith("/static/") and not path.startswith("/static/documents/"):
        return None

    if core.require_admin():
        return None

    if path.startswith("/admin") or path == "/qr":
        return core.redirect(core.url_for("client_login", next=_safe_next_url(path)))

    if path.startswith("/documents/") or path.startswith("/static/documents/"):
        if _client_logged():
            return None
        return core.redirect(core.url_for("client_login", next=_safe_next_url(path)))

    if _client_logged():
        if path == "/":
            return None
        return core.app.response_class(
            "Nemáte oprávnění k této části webu.",
            status=403,
            mimetype="text/plain",
        )

    next_url = path
    if request.query_string:
        next_url += "?" + request.query_string.decode("utf-8", errors="ignore")
    return core.redirect(core.url_for("client_login", next=_safe_next_url(next_url)))


@app.route("/qr-code/<plate>.svg")
def qr_code(plate):
    vehicle = core.get_vehicle(plate)
    if not vehicle:
        return core.app.response_class("Vozidlo nenalezeno.", status=404, mimetype="text/plain")
    public_url = f"{PUBLIC_BASE_URL}/v/{vehicle.get('id')}"
    qr = qrcode.QRCode(box_size=10, border=3)
    qr.add_data(public_url)
    qr.make(fit=True)
    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    image.save(stream)
    response = core.app.response_class(stream.getvalue(), mimetype="image/svg+xml")
    if request.args.get("download") == "1":
        safe_plate = str(vehicle.get("spz") or vehicle.get("id") or "vozidlo").replace(" ", "_")
        response.headers["Content-Disposition"] = f'attachment; filename="QR_{safe_plate}.svg"'
    return response


def run_daily_sync(force=False):
    now_local = datetime.now(LOCAL_TZ)
    run_key = now_local.strftime("%Y-%m-%d") if not force else now_local.strftime("manual-%Y%m%d-%H%M%S")
    if not db_storage.claim_job("daily-sync", run_key):
        return {"skipped": True, "run_key": run_key}

    result = {"run_key": run_key}
    try:
        stk = core.refresh_stk_all()
        vignette = service_fleet.refresh_vignettes(core)
        push = push_notifications.send_due_notifications(core.load_vehicles())
        result.update({"stk": stk, "vignette": vignette, "push": push})
        errors = list(stk.get("errors") or []) + list(vignette.get("errors") or []) + list(push.get("errors") or [])
        # Výpadek externího API nebere poslední uložená data, ale běh si evidujeme jako dokončený.
        db_storage.finish_job("daily-sync", run_key, "ok", result)
        result["errors"] = errors
        return result
    except Exception as exc:
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        db_storage.finish_job("daily-sync", run_key, "failed", result)
        app.logger.exception("Daily sync failed")
        return result


@app.route("/admin/sync-now", methods=["POST"])
def admin_sync_now():
    if not core.require_admin():
        return core.redirect(core.url_for("client_login", next="/admin"))
    result = run_daily_sync(force=True)
    stk_count = (result.get("stk") or {}).get("updated", 0)
    vignette_count = (result.get("vignette") or {}).get("updated", 0)
    push_count = (result.get("push") or {}).get("sent", 0)
    core.flash(f"Synchronizace hotová: STK {stk_count}, eDalnice {vignette_count}, push {push_count}.")
    return core.redirect(core.url_for("admin"))


@app.route("/healthz")
def healthz():
    try:
        return core.jsonify({
            "ok": True,
            "vehicle_count": db_storage.count_vehicles(),
            "persistence": "postgresql",
        })
    except Exception as exc:
        return core.jsonify({"ok": False, "error": str(exc)}), 500


def _background_scheduler():
    # Render web proces drží tento lehký plánovač. Databázový claim zabrání duplicitě
    # při více gunicorn workerech. Denní synchronizace proběhne po 07:00 českého času.
    while True:
        try:
            now_local = datetime.now(LOCAL_TZ)
            if now_local.hour >= 7:
                run_daily_sync(force=False)
        except Exception:
            app.logger.exception("Background scheduler failed")
        time.sleep(300)


if BACKGROUND_JOBS_ENABLED:
    threading.Thread(
        target=_background_scheduler,
        name="crocodille-daily-sync",
        daemon=True,
    ).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
