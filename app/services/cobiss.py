"""COBISS REST API client for exporting bibliographic records as RIS JSON."""

import asyncio
import os
from collections.abc import Mapping
from typing import Any

from app.services.http_client import get, post

API_BASE_URL = os.getenv("COBISS_API_BASE_URL", "https://ws.cobiss.net/cobiss-rest").rstrip("/")
DATABASE = os.getenv("COBISS_DATABASE", "si")


async def create_session(username: str, password: str) -> str:
    """Authenticate with COBISS and return the CSESSIONID for API requests."""
    response = await post(
        f"{API_BASE_URL}/auth",
        json={"username": username, "password": password, "version": 3.0, "language": "slv"},
        headers={"Accept": "application/json"},
    )
    if response.status_code == 403:
        raise RuntimeError("COBISS authentication failed: check cobiss.username and cobiss.password")
    response.raise_for_status()
    payload = response.json()
    session_id = payload.get("csessionid")
    if not session_id:
        raise RuntimeError("COBISS authentication response did not contain csessionid")
    return str(session_id)


def _first(record: Mapping[str, list[str]], field: str) -> str | None:
    values = record.get(field) or []
    return str(values[0]).strip() if values and str(values[0]).strip() else None


def _values(record: Mapping[str, list[str]], *fields: str) -> list[str]:
    return [str(value).strip() for field in fields for value in record.get(field, []) if str(value).strip()]


def _normalise_ris(payload: Any) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        raise RuntimeError("COBISS RIS export response was not a JSON object")
    return {
        str(key): [str(item) for item in value] if isinstance(value, list) else [str(value)]
        for key, value in payload.items()
        if value is not None
    }


def _record_from_ris(requested_id: int, ris: dict[str, list[str]]) -> dict[str, Any]:
    authors = _values(ris, "AU", "A1")
    title = _first(ris, "TI") or _first(ris, "T1")
    record_id = _first(ris, "ID") or str(requested_id)
    details = {
        "Avtor": "; ".join(authors),
        "Naslov": title,
        "Leto": _first(ris, "PY") or _first(ris, "Y1"),
        "Tip dela": _first(ris, "TY"),
        "DOI": _first(ris, "DO"),
        "Vir": _first(ris, "JO") or _first(ris, "T2") or _first(ris, "JF"),
        "Jezik": _first(ris, "LA"),
        "Povezava": _first(ris, "UR"),
    }
    return {
        "id": int(record_id) if record_id.isdigit() else record_id,
        "title": title,
        "authors": authors,
        "url": f"{API_BASE_URL}/ris/{requested_id}",
        "podrobni_podatki": {key: value for key, value in details.items() if value},
        "ris": ris,
    }


async def fetch_cobiss_record(cobiss_id: int, session_id: str) -> dict[str, Any]:
    """Fetch one COBISS record through the documented RIS JSON endpoint."""
    response = await get(
        f"{API_BASE_URL}/ris/{cobiss_id}",
        params={"database": DATABASE} if DATABASE else None,
        headers={"CSESSIONID": session_id, "Accept": "application/json"},
    )
    if response.status_code == 401:
        raise RuntimeError("COBISS session has expired; request new tokens from POST /auth/tokens")
    response.raise_for_status()
    return _record_from_ris(cobiss_id, _normalise_ris(response.json()))


async def fetch_cobiss_records(cobiss_ids: list[int], session_id: str, concurrency: int = 5):
    """Export several records concurrently, returning successful records and errors."""
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(cobiss_id: int):
        async with semaphore:
            return await fetch_cobiss_record(cobiss_id, session_id)

    results = await asyncio.gather(*(fetch_one(cobiss_id) for cobiss_id in cobiss_ids), return_exceptions=True)
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
        else:
            records.append(result)
    return records, errors
