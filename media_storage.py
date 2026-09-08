import base64
import hashlib
import os
from pathlib import Path

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "jakubwurm-coder/crocodille-rent").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip() or "main"
GITHUB_API = "https://api.github.com"


def enabled():
    return bool(GITHUB_TOKEN and GITHUB_REPO and GITHUB_BRANCH)


def backend_name():
    return "github" if enabled() else "ephemeral-local"


def _headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _url(repo_path):
    safe_path = str(repo_path or "").replace("\\", "/").lstrip("/")
    return f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{safe_path}"


def _metadata(repo_path):
    if not enabled():
        return None
    response = requests.get(
        _url(repo_path),
        headers=_headers(),
        params={"ref": GITHUB_BRANCH},
        timeout=20,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _git_blob_sha(data):
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def sync_file(local_path, repo_path, message):
    if not enabled():
        return {"ok": False, "reason": "github_not_configured"}

    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return {"ok": False, "reason": "local_file_missing"}

    data = path.read_bytes()
    existing = _metadata(repo_path)
    if existing and existing.get("sha") == _git_blob_sha(data):
        return {"ok": True, "changed": False, "sha": existing.get("sha")}

    payload = {
        "message": str(message or "Aktualizace média CROCODILLE RENT"),
        "content": base64.b64encode(data).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if existing and existing.get("sha"):
        payload["sha"] = existing["sha"]

    response = requests.put(
        _url(repo_path),
        headers=_headers(),
        json=payload,
        timeout=45,
    )
    response.raise_for_status()
    body = response.json()
    return {
        "ok": True,
        "changed": True,
        "sha": ((body.get("content") or {}).get("sha") or ""),
    }


def delete_file(repo_path, message):
    if not enabled():
        return {"ok": False, "reason": "github_not_configured"}

    existing = _metadata(repo_path)
    if not existing or not existing.get("sha"):
        return {"ok": True, "changed": False}

    response = requests.delete(
        _url(repo_path),
        headers=_headers(),
        json={
            "message": str(message or "Smazání média CROCODILLE RENT"),
            "sha": existing["sha"],
            "branch": GITHUB_BRANCH,
        },
        timeout=30,
    )
    response.raise_for_status()
    return {"ok": True, "changed": True}
