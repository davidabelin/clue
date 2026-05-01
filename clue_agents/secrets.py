"""Secret-loading helpers for production credentials."""

from __future__ import annotations

import os
from functools import lru_cache


def resolve_openai_api_key(*, api_key: str = "") -> str:
    """Prefer an explicit key, then Clue-owned env, then Clue Secret Manager indirection."""

    direct_value = str(api_key or os.getenv("OPENAI_CLUE_SA_KEY", "")).strip()
    if direct_value:
        return direct_value
    secret_version = str(os.getenv("OPENAI_CLUE_SA_KEY_SECRET_VERSION", "")).strip()
    if not secret_version:
        return ""
    return _access_secret_version(secret_version)


def resolve_openai_project_id(*, project_id: str = "") -> str:
    """Return the OpenAI project id that Clue traffic should be attributed to."""

    return str(project_id or os.getenv("OPENAI_CLUE_PROJECT_ID", "")).strip()


def _create_secret_manager_client():
    """Construct the lazy Secret Manager client only when a secret is needed."""

    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


@lru_cache(maxsize=None)
def _access_secret_version(secret_version: str) -> str:
    """Read and cache one Secret Manager version, returning an empty string on failure."""

    try:
        client = _create_secret_manager_client()
        response = client.access_secret_version(request={"name": secret_version})
        payload = getattr(getattr(response, "payload", None), "data", b"")
        if isinstance(payload, bytes):
            return payload.decode("utf-8").strip()
        return str(payload or "").strip()
    except Exception:
        return ""
