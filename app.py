import hmac
import io
import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import qrcode
import qrcode.image.svg
from flask import g, request, session

import db_storage
import legacy_app as core
import media_storage
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


def _media_repo_path(kind, filename):
    safe = str(filename or "").strip().replace("\\", "/").split("/")[-1]
    return f"static/{kind}/{safe}" if safe else ""


def _capture_media_delete(kind, filename):
    repo_path = _media_repo_path(kind, filename)
    if not repo_path:
        return
    pending = getattr(g, "media_delete", [])
    pending.append(repo_path)
    g.media_delete = pending


@app.before_request
def capture_media_mutation_state():
    if request.method != "POST" or not core.require_admin():
        return None

    path = request.path or ""

    match = re.fullmatch(r"/admin/([^/]+)/documents/([^/]+)/delete", path)
    if match:
        vehicle = core.get_vehicle(match.group(1))
        if vehicle:
            target = next(
                (
                    doc
                    for doc in vehicle.get("documents", [])
                    if str(doc.get("id")) == str(match.group(2))
                ),
                None,
            )
            if target:
                _capture_media_delete("documents", target.get("filename"))
        return None

    match = re.fullmatch(r"/admin/([^/]+)/delete", path)
    if match:
        vehicle = core.get_vehicle(match.group(1))
        if vehicle:
            _capture_media_delete("images", vehicle.get("photo"))
            for document in vehicle.get("documents", []):
                _capture_media_delete("documents", document.get("filename"))
        return None

    match = re.fullmatch(r"/admin/([^/]+)", path)
    if match:
        vehicle = core.get_vehicle(match.group(1))
        if vehicle and vehicle.get("photo"):
            g.media_old_photo = str(vehicle.get("photo") or "")
    return None


def _sync_vehicle_media(vehicle):
    if not media_storage.enabled() or not vehicle:
        return

    photo = str(vehicle.get("photo") or "").strip()
    if photo:
        local_photo = core.IMAGES_DIR / photo
        if local_photo.exists():
            media_storage.sync_file(
                local_photo,
                _media_repo_path("images", photo),
                f"CROCODILLE RENT: uložit fotografii {vehicle.get('spz') or vehicle.get('id')}",
            )

    for document in vehicle.get("documents", []):
        filename = str(document.get("filename") or "").strip()
        if not filename:
            continue
        local_document = core.DOCS_DIR / filename
        if local_document.exists():
            media_storage.sync_file(
                local_document,
                _media_repo_path("documents", filename),
                f"CROCODILLE RENT: uložit dokument {vehicle.get('spz') or vehicle.get('id')}",
            )


@app.after_request
def persist_media_mutations(response):
    if request.method != "POST" or response.status_code >= 400:
        return response

    try:
        for repo_path in getattr(g, "media_delete", []):
            if media_storage.enabled():
                media_storage.delete_file(repo_path, f"CROCODILLE RENT: smazat {repo_path}")

        path = request.path or ""
        match = re.fullmatch(r"/admin/([^/]+)", path)
        if match:
            vehicle = core.get_vehicle(match.group(1))
            if vehicle:
                old_photo = str(getattr(g, "media_old_photo", "") or "")
                new_photo = str(vehicle.get("photo") or "")
                if old_photo and new_photo and old_photo != new_photo and media_storage.enabled():
                    media_storage.delete_file(
                        _media_repo_path("images", old_photo),
                        f"CROCODILLE RENT: smazat starou fotografii {old_photo}",
                    )
                _sync_vehicle_media(vehicle)

        match = re.fullmatch(r"/admin/([^/]+)/documents/add", path)
        if match:
            _sync_vehicle_media(core.get_vehicle(match.group(1)))
    except Exception:
        app.logger.exception("Media persistence sync failed")

    return response


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
        # Výpadek externího API nemaže poslední uložená data; běh se ale eviduje jako dokončený.
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
            "media_storage": media_storage.backend_name(),
            "push_configured": push_notifications.configured(),
        })
    except Exception as exc:
        return core.jsonify({"ok": False, "error": str(exc)}), 500


def _background_scheduler():
    # Render web proces drží lehký plánovač. Databázový claim zabrání duplicitě
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

try:
    app.logger.info(
        "CROCODILLE RENT startup database=postgresql media=%s push=%s background_jobs=%s",
        media_storage.backend_name(),
        "configured" if push_notifications.configured() else "not-configured",
        BACKGROUND_JOBS_ENABLED,
    )
except Exception:
    pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
