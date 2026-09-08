import base64
import os
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests

EDALNICE_COUNTRY_ID_CZ = "3906ba89-153c-4038-8e36-0ca1deb76076"
EDALNICE_INDEX_URL = "https://edalnice.gov.cz/"
EDALNICE_AUTH_URL = "https://auth.edalnice.gov.cz/auth/connect/token"
EDALNICE_VALIDATION_URL = (
    "https://eshop.edalnice.gov.cz/api/v3/charge_registrations/"
    + EDALNICE_COUNTRY_ID_CZ
    + "/"
)

_token_cache = {"token": "", "expires_at": 0.0}
_client_cache = {"client_id": "", "client_secret": "", "expires_at": 0.0}


def _headers(accept="*/*"):
    return {
        "Accept": accept,
        "Accept-Language": "cs",
        "Referer": "https://edalnice.gov.cz/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    }


def _parse_iso(value):
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


def _client_credentials(force=False):
    now = time.time()
    if (
        not force
        and _client_cache["client_id"]
        and _client_cache["client_secret"]
        and _client_cache["expires_at"] > now
    ):
        return _client_cache["client_id"], _client_cache["client_secret"]

    configured = os.environ.get("EDALNICE_CLIENT_BASIC", "").strip()
    if ":" in configured:
        client_id, client_secret = configured.split(":", 1)
        if client_id and client_secret:
            _client_cache.update({
                "client_id": client_id,
                "client_secret": client_secret,
                "expires_at": now + 86400,
            })
            return client_id, client_secret

    response = requests.get(
        EDALNICE_INDEX_URL,
        headers=_headers("text/html,application/xhtml+xml"),
        timeout=20,
    )
    response.raise_for_status()
    html = response.text
    script_urls = []
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        url = urljoin(EDALNICE_INDEX_URL, src)
        if url not in script_urls:
            script_urls.append(url)

    patterns = [
        r'["\'](eshop\.client):([^"\']+)["\']',
        r'\b(eshop\.client):([A-Za-z0-9._~!*()\-]+)',
    ]
    last_error = None
    for script_url in script_urls[:80]:
        try:
            script = requests.get(script_url, headers=_headers("*/*"), timeout=20)
            script.raise_for_status()
            text = script.text
            if "auth.edalnice.gov.cz/auth/connect/token" not in text and "eshop.client" not in text:
                continue
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    client_id = match.group(1)
                    client_secret = match.group(2)
                    _client_cache.update({
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "expires_at": now + 21600,
                    })
                    return client_id, client_secret
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "eDalnice: v aktuálním webu nebyl nalezen veřejný OAuth klient"
        + (f" ({last_error})" if last_error else "")
    )


def _token(force=False):
    now = time.time()
    if not force and _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    client_id, client_secret = _client_credentials(force=force)
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    response = requests.post(
        EDALNICE_AUTH_URL,
        data={"grant_type": "client_credentials", "scope": "eshop.api"},
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": _headers()["User-Agent"],
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
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + max(60, int(payload.get("expires_in") or 300))
    return token


def _collect_charge_dicts(node):
    found = []
    if isinstance(node, dict):
        if ("validSince" in node or "valid_since" in node) and ("validUntil" in node or "valid_until" in node):
            found.append(node)
        for value in node.values():
            found.extend(_collect_charge_dicts(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_collect_charge_dicts(value))
    return found


def lookup_vignette(spz):
    plate = re.sub(r"\s+", "", str(spz or "").strip().upper())
    if not plate:
        raise ValueError("Chybí SPZ.")

    def make_request(access_token):
        headers = _headers("*/*")
        headers.update({
            "Authorization": f"Bearer {access_token}",
            "Origin": "https://edalnice.gov.cz",
        })
        return requests.get(EDALNICE_VALIDATION_URL + plate, headers=headers, timeout=20)

    access_token = _token()
    response = make_request(access_token)
    if response.status_code == 401:
        _token_cache.update({"token": "", "expires_at": 0.0})
        response = make_request(_token(force=True))
    if not response.ok:
        body = (response.text or "")[:500].replace("\n", " ")
        raise RuntimeError(f"eDalnice API HTTP {response.status_code} pro {plate}: {body}")

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"eDalnice API pro {plate} nevrátilo platné JSON: {exc}") from exc

    now = datetime.now(timezone.utc)
    intervals = []
    for charge in _collect_charge_dicts(payload):
        start = _parse_iso(charge.get("validSince") or charge.get("valid_since"))
        end = _parse_iso(charge.get("validUntil") or charge.get("valid_until"))
        if start and end:
            intervals.append((start, end))

    # Odstranění duplicit a řazení podle začátku.
    intervals = sorted(set((start.isoformat(), end.isoformat()) for start, end in intervals))
    intervals = [(_parse_iso(start), _parse_iso(end)) for start, end in intervals]

    exempt = bool(payload.get("isGivenExemption") or payload.get("is_given_exemption"))
    current = [(start, end) for start, end in intervals if start <= now <= end]
    future = [(start, end) for start, end in intervals if start > now]

    if exempt:
        return {
            "plate": plate,
            "status": "exempt",
            "is_exempt": True,
            "valid_until": None,
            "future_from": None,
            "future_until": None,
        }

    if current:
        effective_end = max(end for _, end in current)
        # Pokud navazující známka začíná nejpozději následující den, bereme ji jako
        # souvislé krytí a prodloužíme zobrazené "Platná do".
        for start, end in future:
            if start.date() <= effective_end.date() + timedelta(days=1):
                effective_end = max(effective_end, end)
            else:
                break
        first_future = future[0] if future else (None, None)
        return {
            "plate": plate,
            "status": "valid",
            "is_exempt": False,
            "valid_until": effective_end,
            "future_from": first_future[0],
            "future_until": first_future[1],
        }

    if future:
        start, end = future[0]
        return {
            "plate": plate,
            "status": "future",
            "is_exempt": False,
            "valid_until": end,
            "future_from": start,
            "future_until": end,
        }

    return {
        "plate": plate,
        "status": "missing",
        "is_exempt": False,
        "valid_until": None,
        "future_from": None,
        "future_until": None,
    }


def apply_vignette_result(vehicle, result):
    vehicle["vignette_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    vehicle["vignette_source"] = "edalnice"
    vehicle["vignette_status"] = result.get("status") or "missing"
    vehicle["vignette_is_exempt"] = bool(result.get("is_exempt"))
    vehicle["vignette_until"] = (
        result["valid_until"].date().isoformat() if result.get("valid_until") else ""
    )
    vehicle["vignette_future_from"] = (
        result["future_from"].date().isoformat() if result.get("future_from") else ""
    )
    vehicle["vignette_future_until"] = (
        result["future_until"].date().isoformat() if result.get("future_until") else ""
    )
    vehicle.pop("vignette_last_error", None)
    return vehicle


def refresh_vignettes(core):
    vehicles = core.load_vehicles()
    updated = 0
    errors = []
    changed = False
    for vehicle in vehicles:
        spz = str(vehicle.get("spz") or "").strip().upper()
        if not spz or not core.is_active_vehicle(vehicle):
            continue
        try:
            apply_vignette_result(vehicle, lookup_vignette(spz))
            updated += 1
            changed = True
        except Exception as exc:
            # Poslední úspěšná data zůstávají nedotčená.
            vehicle["vignette_last_error"] = str(exc)[:300]
            errors.append(f"{spz}: {type(exc).__name__}: {exc}")
            changed = True
    if changed:
        core.save_vehicles(vehicles)
    return {"updated": updated, "errors": errors}
