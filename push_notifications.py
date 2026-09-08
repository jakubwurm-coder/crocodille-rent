import os
import time
from datetime import date, datetime

import httpx
import jwt
from flask import jsonify, request

import db_storage

DUE_DAYS = {30, 14, 7, 6, 5, 4, 3, 2, 1, 0}
LABELS = {
    "stk_until": "STK",
    "vignette_until": "Dálniční známka",
    "insurance_until": "Pojištění",
    "assistance_until": "Asistence",
}


def _parse_date(value):
    raw = str(value or "").strip()[:10]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


def configured():
    return all(
        str(os.environ.get(key, "")).strip()
        for key in ("APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY")
    )


def _private_key():
    return os.environ.get("APNS_PRIVATE_KEY", "").replace("\\n", "\n").strip()


def _base_url():
    env = os.environ.get("APNS_ENV", "sandbox").strip().lower()
    return "https://api.push.apple.com" if env in ("production", "prod") else "https://api.sandbox.push.apple.com"


def _provider_token():
    team_id = os.environ.get("APNS_TEAM_ID", "").strip()
    key_id = os.environ.get("APNS_KEY_ID", "").strip()
    return jwt.encode(
        {"iss": team_id, "iat": int(time.time())},
        _private_key(),
        algorithm="ES256",
        headers={"kid": key_id},
    )


def _send(token, title, body, data):
    bundle_id = os.environ.get("APNS_BUNDLE_ID", "cz.jakubwurm.vansrenting.crocodille").strip()
    headers = {
        "authorization": f"bearer {_provider_token()}",
        "apns-topic": bundle_id,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
        },
        **data,
    }
    with httpx.Client(http2=True, timeout=20) as client:
        response = client.post(f"{_base_url()}/3/device/{token}", headers=headers, json=payload)
    reason = ""
    try:
        reason = (response.json() or {}).get("reason", "")
    except Exception:
        pass
    return response.status_code, reason


def register_routes(app):
    @app.post("/api/v1/devices/register")
    def register_device():
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("token") or "").strip().lower()
        if not token or any(ch not in "0123456789abcdef" for ch in token):
            return jsonify({"ok": False, "error": "Neplatný device token."}), 400
        db_storage.register_device_token(
            token,
            platform=str(payload.get("platform") or "ios"),
            app_version=str(payload.get("app_version") or ""),
        )
        return jsonify({"ok": True, "push_configured": configured()})

    @app.post("/api/v1/devices/unregister")
    def unregister_device():
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("token") or "").strip().lower()
        if token:
            db_storage.disable_device_token(token)
        return jsonify({"ok": True})


def _due_alerts(vehicles):
    today = date.today()
    for vehicle in vehicles:
        if str(vehicle.get("status") or "").lower().startswith(("vráceno", "vraceno")):
            continue
        for key, label in LABELS.items():
            expiration = _parse_date(vehicle.get(key))
            if not expiration:
                continue
            days = (expiration - today).days
            if days not in DUE_DAYS:
                continue
            if days == 0:
                message = f"{label} pro {vehicle.get('spz') or 'vozidlo'} končí dnes."
            elif days == 1:
                message = f"{label} pro {vehicle.get('spz') or 'vozidlo'} končí zítra."
            else:
                message = f"{label} pro {vehicle.get('spz') or 'vozidlo'} končí za {days} dní."
            alert_key = f"v2:{vehicle.get('id')}:{key}:{expiration.isoformat()}:{days}"
            yield {
                "alert_key": alert_key,
                "title": f"{vehicle.get('spz') or 'Vozidlo'} · {label}",
                "body": message,
                "vehicle_id": str(vehicle.get("id") or ""),
                "spz": str(vehicle.get("spz") or ""),
                "kind": key,
                "date": expiration.isoformat(),
                "days": days,
            }


def send_due_notifications(vehicles):
    if not configured():
        return {"configured": False, "sent": 0, "errors": ["APNs není nakonfigurováno na Renderu."]}

    tokens = db_storage.active_device_tokens()
    alerts = list(_due_alerts(vehicles))
    sent = 0
    errors = []
    for token in tokens:
        for alert in alerts:
            if db_storage.notification_was_sent(token, alert["alert_key"]):
                continue
            try:
                status, reason = _send(
                    token,
                    alert["title"],
                    alert["body"],
                    {
                        "vehicle_id": alert["vehicle_id"],
                        "spz": alert["spz"],
                        "kind": alert["kind"],
                        "date": alert["date"],
                        "days": alert["days"],
                    },
                )
                if status == 200:
                    db_storage.mark_notification_sent(token, alert["alert_key"])
                    sent += 1
                elif status in (400, 410) and reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
                    db_storage.disable_device_token(token)
                    errors.append(f"Token deaktivován: {reason}")
                    break
                else:
                    errors.append(f"APNs HTTP {status}: {reason or 'bez důvodu'}")
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    return {"configured": True, "tokens": len(tokens), "alerts": len(alerts), "sent": sent, "errors": errors}
