import hmac
import io
import os
import re
import threading
import time
from datetime import datetime, timezone, date

import qrcode
import qrcode.image.svg
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from flask import request, session

import legacy_app as core
import service_fleet

app = core.app

# Externí zdroje používáme jen k doplnění/obnovení dat. Primárním zdrojem pro web
# a iOS je PostgreSQL databáze. Pokud je platnost daleko, API se zbytečně nevolá.
service_fleet.VIGNETTE_REFRESH_SECONDS = 10**9
service_fleet._vignette_last_started = time.time()

OVERVIEW_REFRESH_MIN_SECONDS = int(os.environ.get("OVERVIEW_REFRESH_MIN_SECONDS", "120"))
CACHE_REFRESH_WINDOW_DAYS = int(os.environ.get("CACHE_REFRESH_WINDOW_DAYS", "30"))
CACHE_NEAR_EXPIRY_DAYS = int(os.environ.get("CACHE_NEAR_EXPIRY_DAYS", "7"))
CACHE_DAILY_SECONDS = int(os.environ.get("CACHE_DAILY_SECONDS", "86400"))
CACHE_NEAR_SECONDS = int(os.environ.get("CACHE_NEAR_SECONDS", "21600"))
CACHE_EXPIRED_SECONDS = int(os.environ.get("CACHE_EXPIRED_SECONDS", "3600"))
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


def _parse_external_date(value):
    raw = str(value or "").strip()[:10]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


def _cache_refresh_due(vehicle, kind):
    """Rozhodne, zda má smysl sahat na externí zdroj.

    - nové/neověřené vozidlo: ihned
    - platnost delší než 30 dní: pouze databáze, bez API
    - 8 až 30 dní: nejvýše 1x denně
    - 0 až 7 dní: nejvýše 1x za 6 hodin
    - po expiraci / bez platnosti po dřívějším ověření: nejvýše 1x za hodinu
    """
    if kind == "stk":
        source_key = "stk_source"
        checked_key = "stk_checked_at"
        until_key = "stk_until"
    elif kind == "vignette":
        source_key = "vignette_source"
        checked_key = "vignette_checked_at"
        until_key = "vignette_until"
    else:
        return False

    source = str(vehicle.get(source_key) or "").strip()
    checked = _parse_external_timestamp(vehicle.get(checked_key))
    until = _parse_external_date(vehicle.get(until_key))

    # Nově založené vozidlo nebo starý ruční záznam se ověří okamžitě.
    if not source or not checked:
        return True

    now = datetime.now(timezone.utc)
    age_seconds = max(0.0, (now - checked).total_seconds())

    # Ověřený stav bez data (např. známka nenalezena) zkoušíme pravidelně,
    # ale ne při každém otevření stránky.
    if not until:
        return age_seconds >= CACHE_DAILY_SECONDS

    days_left = (until - date.today()).days
    if days_left > CACHE_REFRESH_WINDOW_DAYS:
        return False
    if days_left > CACHE_NEAR_EXPIRY_DAYS:
        return age_seconds >= CACHE_DAILY_SECONDS
    if days_left >= 0:
        return age_seconds >= CACHE_NEAR_SECONDS
    return age_seconds >= CACHE_EXPIRED_SECONDS


def _external_data_state(vehicle, kind):
    """Zobrazení považuje uložená ověřená data za platná, dokud není čas je obnovit."""
    if kind == "stk":
        checked_key = "stk_checked_at"
        source_key = "stk_source"
        error_key = "stk_last_error"
    elif kind == "vignette":
        checked_key = "vignette_checked_at"
        source_key = "vignette_source"
        error_key = "vignette_last_error"
    else:
        return {"fresh": True, "loading": False, "error": False, "checked_at": ""}

    checked_raw = str(vehicle.get(checked_key) or "").strip()
    verified = bool(vehicle.get(source_key) and checked_raw)
    due = _cache_refresh_due(vehicle, kind) if verified else True

    return {
        "fresh": verified and not due,
        "loading": not verified,
        # Chyba externí služby nesmí znehodnotit poslední uložená data.
        "error": bool(vehicle.get(error_key)) and not verified,
        "checked_at": checked_raw,
    }


def _has_unverified_external_data():
    """Nově přidané vozidlo nesmí čekat na globální 120s throttle."""
    try:
        for vehicle in core.load_vehicles():
            if not core.is_active_vehicle(vehicle):
                continue
            vin = str(vehicle.get("vin") or "").strip().upper()
            spz = str(vehicle.get("spz") or "").strip().upper()
            if len(vin) == 17 and (not vehicle.get("stk_source") or not vehicle.get("stk_checked_at")):
                return True
            if spz and (not vehicle.get("vignette_source") or not vehicle.get("vignette_checked_at")):
                return True
    except Exception:
        return False
    return False


def _refresh_main_stk_from_datova_kostka(force=False):
    """Obnoví jen STK, které jsou nové nebo se blíží konci platnosti."""
    vehicles = core.load_vehicles()
    changed = False
    updated = 0
    skipped = 0
    errors = []

    for vehicle in vehicles:
        vin = str(vehicle.get("vin") or "").strip().upper()
        if len(vin) != 17 or not core.is_active_vehicle(vehicle):
            continue
        if not force and not _cache_refresh_due(vehicle, "stk"):
            skipped += 1
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

            # U nového vozidla rovnou doplníme i RZ z Datové kostky, aby se
            # následně ve stejném cyklu mohla ověřit eDalnice.
            if not str(vehicle.get("spz") or "").strip() and basic.get("spz"):
                vehicle["spz"] = str(basic.get("spz") or "").strip().upper()

            if inspection_until:
                vehicle["stk_until"] = inspection_until

            updated += 1
            changed = True
            print(
                f"STK cache refresh OK: {vin} spz={vehicle.get('spz') or '-'} "
                f"until={vehicle.get('stk_until') or '-'}"
            )
        except Exception as exc:
            errors.append(f"{vin}: {type(exc).__name__}: {exc}")
            vehicle["stk_last_error"] = str(exc)[:300]
            # checked_at neposouváme: při chybě zůstává poslední úspěšné ověření.
            changed = True
            print(f"STK cache refresh error: {vin}: {type(exc).__name__}: {exc}")

    if changed:
        core.save_vehicles(
            vehicles,
            f"Cache STK z Datové kostky ({updated} obnoveno, {skipped} z DB)",
        )

    return {"updated": updated, "skipped": skipped, "errors": errors}


def _start_overview_background_refresh():
    global _overview_last_started

    now = time.time()
    if (
        now - _overview_last_started < OVERVIEW_REFRESH_MIN_SECONDS
        and not _has_unverified_external_data()
    ):
        return
    if not _overview_refresh_lock.acquire(blocking=False):
        return

    _overview_last_started = now

    def worker():
        try:
            # Nejdřív Datová kostka: u nového auta může doplnit SPZ.
            stk_result = _refresh_main_stk_from_datova_kostka(force=False)
            if stk_result.get("errors"):
                app.logger.warning(
                    "Datova kostka STK cache refresh: updated=%s skipped=%s errors=%s",
                    stk_result.get("updated", 0),
                    stk_result.get("skipped", 0),
                    len(stk_result.get("errors") or []),
                )

            # eDalnice už respektuje databázovou cache a neověřuje známky daleko
            # před expirací.
            vignette_result = service_fleet._refresh_main_vignettes(core, force=False)
            service_fleet._vignette_last_started = time.time()
            if vignette_result.get("errors"):
                app.logger.warning(
                    "eDalnice cache refresh: updated=%s skipped=%s errors=%s",
                    vignette_result.get("updated", 0),
                    vignette_result.get("skipped", 0),
                    len(vignette_result.get("errors") or []),
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
    admin_overview = path == "/admin" and core.require_admin()
    mobile_overview = path == "/api/vehicles"
    if web_overview or web_vehicle_detail or admin_overview or mobile_overview:
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
