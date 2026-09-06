import hmac
import io
import os
import re
import threading
import time
from datetime import datetime, timezone

import qrcode
import qrcode.image.svg
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from flask import request, session

import legacy_app as core
import service_fleet

app = core.app

# Dálniční známky i STK obnovujeme po otevření přehledu vozidel nebo detailu vozidla,
# vždy na pozadí, aby se web ani iOS neblokovaly čekáním na externí API.
# Původní periodický refresh eDalnice vypínáme, aby se API nevolalo dvakrát.
service_fleet.VIGNETTE_REFRESH_SECONDS = 10**9
service_fleet._vignette_last_started = time.time()

OVERVIEW_REFRESH_MIN_SECONDS = int(os.environ.get("OVERVIEW_REFRESH_MIN_SECONDS", "120"))
EXTERNAL_DATA_FRESH_SECONDS = int(os.environ.get("EXTERNAL_DATA_FRESH_SECONDS", "300"))
_overview_refresh_lock = threading.Lock()
_overview_last_started = 0.0


def _parse_external_timestamp(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _external_data_state(vehicle, kind):
    """Vrací stav čerstvosti dat pro zobrazení v kartě vozidla."""
    if kind == "stk":
        checked_key = "stk_checked_at"
        error_key = "stk_last_error"
    elif kind == "vignette":
        checked_key = "vignette_checked_at"
        error_key = "vignette_last_error"
    else:
        return {"fresh": True, "loading": False, "error": False, "checked_at": ""}

    checked_raw = str(vehicle.get(checked_key) or "").strip()
    checked = _parse_external_timestamp(checked_raw)
    now = datetime.now(timezone.utc)
    fresh = bool(
        checked
        and (now - checked).total_seconds() <= EXTERNAL_DATA_FRESH_SECONDS
        and not vehicle.get(error_key)
    )

    return {
        "fresh": fresh,
        "loading": not fresh,
        "error": bool(vehicle.get(error_key)),
        "checked_at": checked_raw,
    }


def _refresh_main_stk_from_datova_kostka():
    """Načte STK podle VIN z Datové kostky a uloží ji do hlavní databáze vozidel."""
    vehicles = core.load_vehicles()
    changed = False
    updated = 0
    errors = []

    for vehicle in vehicles:
        vin = str(vehicle.get("vin") or "").strip().upper()
        if len(vin) != 17 or not core.is_active_vehicle(vehicle):
            continue

        try:
            dk = core.make_datova_kostka(vin, core.fetch_datova_kostka(vin))
            basic = dk.get("basic") or {}
            inspection_until = str(basic.get("inspection_until") or "").strip()[:10]
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

            vehicle["datova_kostka"] = dk
            vehicle["stk_checked_at"] = now_iso
            vehicle["stk_source"] = "datova_kostka"
            vehicle.pop("stk_last_error", None)
            if inspection_until:
                vehicle["stk_until"] = inspection_until

            updated += 1
            changed = True
        except Exception as exc:
            errors.append(f"{vin}: {type(exc).__name__}: {exc}")
            vehicle["stk_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            vehicle["stk_last_error"] = str(exc)[:300]
            changed = True

    if changed:
        core.save_vehicles(
            vehicles,
            f"Automatická kontrola STK z Datové kostky ({updated} vozidel)",
        )

    return {"updated": updated, "errors": errors}


def _start_overview_background_refresh():
    global _overview_last_started

    now = time.time()
    if now - _overview_last_started < OVERVIEW_REFRESH_MIN_SECONDS:
        return
    if not _overview_refresh_lock.acquire(blocking=False):
        return

    _overview_last_started = now

    def worker():
        try:
            vignette_result = service_fleet._refresh_main_vignettes(core, force=True)
            service_fleet._vignette_last_started = time.time()
            if vignette_result.get("errors"):
                app.logger.warning(
                    "eDalnice overview refresh: updated=%s errors=%s",
                    vignette_result.get("updated", 0),
                    len(vignette_result.get("errors") or []),
                )

            stk_result = _refresh_main_stk_from_datova_kostka()
            if stk_result.get("errors"):
                app.logger.warning(
                    "Datova kostka STK overview refresh: updated=%s errors=%s",
                    stk_result.get("updated", 0),
                    len(stk_result.get("errors") or []),
                )
        except Exception:
            app.logger.exception("Overview background refresh failed")
        finally:
            _overview_refresh_lock.release()

    threading.Thread(
        target=worker,
        name="vehicle-overview-refresh",
        daemon=True,
    ).start()


@app.before_request
def refresh_external_data_on_vehicle_overview():
    path = request.path or "/"
    web_overview = path == "/" and (core.require_admin() or session.get("client") is True)
    web_vehicle_detail = path.startswith("/v/")
    mobile_overview = path == "/api/vehicles"
    if web_overview or web_vehicle_detail or mobile_overview:
        _start_overview_background_refresh()
    return None


service_fleet.register(core)

VANS_CENTRE_LOGO_URL = "https://img.classistatic.de/api/v1/mo-prod/images/67/671ecf7e-9971-4a73-928f-537b147fa761?rule=mo-640.jpg"
PUBLIC_BASE_URL = "https://vansrenting-crocodille.onrender.com"
CLIENT_USERNAME = os.environ.get("CLIENT_USERNAME", "crocodille").strip() or "crocodille"
CLIENT_PASSWORD = os.environ.get("CLIENT_PASSWORD", "").strip()


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
        "external_data_state": _external_data_state,
    }


@app.before_request
def protect_client_area():
    path = request.path or "/"

    if path in ("/login", "/logout", "/admin/login", "/admin/logout", "/healthz"):
        return None
    if path.startswith("/api/"):
        return None

    if path.startswith("/v/"):
        return None

    if core.require_admin():
        return None

    if path.startswith("/admin"):
        return None

    if _client_logged():
        if _client_route_allowed(path):
            return None
        return core.app.response_class("Nemáte oprávnění k této části webu.", status=403, mimetype="text/plain")

    if path.startswith("/static/") and not path.startswith("/static/documents/"):
        return None

    next_url = path
    if request.query_string:
        next_url += "?" + request.query_string.decode("utf-8", errors="ignore")
    return core.redirect(core.url_for("client_login", next=_safe_next_url(next_url)))


@app.after_request
def hide_service_history_from_client(response):
    """Klientská role nevidí panel servisní historie, admin ano."""
    if (
        _client_logged()
        and not core.require_admin()
        and request.path.startswith("/v/")
        and response.status_code == 200
        and response.mimetype == "text/html"
    ):
        html = response.get_data(as_text=True)
        html = re.sub(
            r'<section class="panel service-panel">.*?</section>',
            '',
            html,
            count=1,
            flags=re.DOTALL,
        )
        response.set_data(html)
        response.headers["Content-Length"] = str(len(response.get_data()))
    return response


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
