import asyncio
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.services.auth import get_jwt
from app.services.cobiss import create_session, fetch_cobiss_records
from app.services.mongo_cache import get_cache_metadata, get_cached_query, save_query_document
from app.services.sircis import fetch_author_basic_data, fetch_ids

app = FastAPI(title="SICRIS / COBISS Records API")


class ApiCredentials(BaseModel):
    username: str
    password: str


class CredentialsBody(BaseModel):
    sicris: ApiCredentials
    cobiss: ApiCredentials


@dataclass(frozen=True)
class ApiTokens:
    sicris_authorization: str
    cobiss_session_id: str


def _normalise_bearer_token(token: str) -> str:
    return token.strip() if token.casefold().startswith("bearer ") else f"Bearer {token.strip()}"


def _require_sicris_authorization(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.casefold().startswith("bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=401, detail="Authorization must be the SICRIS Bearer JWT")
    return authorization.strip()


def _require_tokens(
    authorization: str | None = Header(default=None),
    csessionid: str | None = Header(default=None, alias="CSESSIONID"),
) -> ApiTokens:
    if not csessionid or not csessionid.strip():
        raise HTTPException(status_code=401, detail="Missing CSESSIONID header")
    return ApiTokens(
        sicris_authorization=_require_sicris_authorization(authorization),
        cobiss_session_id=csessionid.strip(),
    )


@app.post("/auth/tokens")
async def create_tokens(credentials: CredentialsBody):
    """Exchange provider credentials for both SICRIS and COBISS API tokens."""
    try:
        sicris_jwt, cobiss_session_id = await asyncio.gather(
            get_jwt(credentials.sicris.username, credentials.sicris.password),
            create_session(credentials.cobiss.username, credentials.cobiss.password),
        )
        return {
            "sicris_authorization": _normalise_bearer_token(sicris_jwt),
            "cobiss_session_id": cobiss_session_id,
            "cobiss_session_header": "CSESSIONID",
            "sicris_jwt_expires_in": 86400,
            "cobiss_session_expires_in": 900,
        }
    except RuntimeError as exc:
        if str(exc).startswith(("SICRIS authentication failed", "COBISS authentication failed")):
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _record_year(record: dict) -> int | None:
    raw_year = (record.get("podrobni_podatki") or {}).get("Leto")
    if raw_year is None:
        return None
    for part in str(raw_year).replace("-", " ").split():
        if part.isdigit() and len(part) == 4:
            return int(part)
    return None


def _record_type(record: dict) -> str | None:
    details = record.get("podrobni_podatki") or {}
    return details.get("Tip dela") or details.get("Vrsta gradiva")


def _record_doi(record: dict) -> str | None:
    doi = (record.get("podrobni_podatki") or {}).get("DOI")
    return str(doi).strip() if doi and str(doi).strip() else None


def _dedupe_records(records: list[dict]) -> list[dict]:
    unique: dict[int | str, dict] = {}
    for record in records:
        unique[record.get("id") or record.get("url") or len(unique)] = record
    return list(unique.values())


def _sort_newest(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda record: (_record_year(record) or 0, record.get("id") or 0), reverse=True)


async def _fetch_author_records(user_number: str, tokens: ApiTokens, limit: int | None = None):
    author = await fetch_author_basic_data(user_number, tokens.sicris_authorization)
    ids = await fetch_ids(user_number, tokens.sicris_authorization)
    selected_ids = ids[:limit] if limit is not None else ids
    results, errors = await fetch_cobiss_records(selected_ids, tokens.cobiss_session_id)
    if selected_ids and not results:
        raise RuntimeError(f"Failed to export all COBISS records. Sample error: {errors[0] if errors else 'unknown'}")
    return author, results, errors


async def _load_author_records(user_number: str, tokens: ApiTokens, background_tasks: BackgroundTasks, limit: int | None = None):
    cached = await get_cached_query(user_number)
    if cached:
        results = cached.get("results", [])
        return cached.get("author"), results[:limit] if limit is not None else results

    author, results, errors = await _fetch_author_records(user_number, tokens, limit)
    if limit is None:
        background_tasks.add_task(
            save_query_document,
            {"user_number": user_number, "author": author, "results": results, "total_count": len(results), "complete": not errors},
        )
    else:
        background_tasks.add_task(_refresh_author_records, user_number, tokens)
    return author, results


async def _refresh_author_records(user_number: str, tokens: ApiTokens):
    author, results, errors = await _fetch_author_records(user_number, tokens)
    await save_query_document(
        {"user_number": user_number, "author": author, "results": results, "total_count": len(results), "complete": not errors}
    )
    return author, results


def _records_response(user_number: str, author: dict | None, results: list[dict]):
    return {"user_number": user_number, "author": author, "count": len(results), "results": results}


async def _records_or_error(user_number: str, tokens: ApiTokens, background_tasks: BackgroundTasks, limit: int | None = None):
    try:
        return await _load_author_records(user_number, tokens, background_tasks, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/records/{user_number}")
async def get_records(user_number: str, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens), limit: int | None = Query(default=None, ge=1)):
    author, results = await _records_or_error(user_number, tokens, background_tasks, limit)
    return _records_response(user_number, author, results)


@app.post("/records/{user_number}/refresh")
async def refresh_records(user_number: str, tokens: ApiTokens = Depends(_require_tokens)):
    try:
        author, results = await _refresh_author_records(user_number, tokens)
        return _records_response(user_number, author, results)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/records/{user_number}/cache")
async def records_cache_metadata(user_number: str, _: ApiTokens = Depends(_require_tokens)):
    try:
        return await get_cache_metadata(user_number)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/records/{user_number}/latest")
async def latest_records(user_number: str, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens), limit: int = Query(default=20, ge=1, le=500)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    return _records_response(user_number, author, _sort_newest(results)[:limit])


@app.post("/records/{user_number}/year/{year}")
async def records_by_year(user_number: str, year: int, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    return _records_response(user_number, author, [record for record in results if _record_year(record) == year])


@app.post("/records/{user_number}/years")
async def records_by_year_summary(user_number: str, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    years: dict[int | str, int] = {}
    for record in results:
        year = _record_year(record) or "unknown"
        years[year] = years.get(year, 0) + 1
    ordered = {year: years[year] for year in sorted((year for year in years if isinstance(year, int)), reverse=True)}
    if "unknown" in years:
        ordered["unknown"] = years["unknown"]
    return {"user_number": user_number, "author": author, "years": ordered}


@app.post("/records/{user_number}/coauthored")
async def coauthored_records(user_number: str, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    return _records_response(user_number, author, [record for record in results if len(record.get("authors", [])) >= 2])


@app.post("/records/{user_number}/solo")
async def solo_records(user_number: str, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    return _records_response(user_number, author, [record for record in results if len(record.get("authors", [])) <= 1])


@app.post("/records/{user_number}/doi")
async def doi_records(user_number: str, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    return _records_response(user_number, author, [record for record in results if _record_doi(record)])


@app.post("/records/{user_number}/search")
async def search_records(user_number: str, background_tasks: BackgroundTasks, q: str = Query(min_length=1), tokens: ApiTokens = Depends(_require_tokens)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    query = q.casefold()
    filtered = [record for record in results if query in str(record.get("title") or "").casefold() or any(query in str(name).casefold() for name in record.get("authors", []))]
    return _records_response(user_number, author, filtered)


@app.post("/records/{user_number}/types")
async def record_type_summary(user_number: str, background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens)):
    author, results = await _records_or_error(user_number, tokens, background_tasks)
    types: dict[str, int] = {}
    for record in results:
        record_type = _record_type(record) or "unknown"
        types[record_type] = types.get(record_type, 0) + 1
    return {"user_number": user_number, "author": author, "types": dict(sorted(types.items(), key=lambda item: item[1], reverse=True))}


async def _records_for_authors(user_numbers: list[str], tokens: ApiTokens, background_tasks: BackgroundTasks):
    authors: dict[str, dict | None] = {}
    per_author_records: list[list[dict]] = []
    for user_number in user_numbers:
        author, records = await _records_or_error(user_number, tokens, background_tasks)
        authors[user_number] = author
        per_author_records.append(records)
    return authors, per_author_records


@app.post("/authors/unique")
async def unique_records_for_authors(background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens), user_numbers: list[str] = Query(min_length=1)):
    authors, per_author_records = await _records_for_authors(user_numbers, tokens, background_tasks)
    unique = _dedupe_records([record for records in per_author_records for record in records])
    return {"user_numbers": user_numbers, "authors": authors, "count": len(unique), "results": unique}


@app.post("/authors/collaborations")
async def collaborations_for_authors(background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens), user_numbers: list[str] = Query(min_length=1)):
    authors, per_author_records = await _records_for_authors(user_numbers, tokens, background_tasks)
    unique = _dedupe_records([record for records in per_author_records for record in records])
    filtered = [record for record in unique if len(record.get("authors", [])) >= 2]
    return {"user_numbers": user_numbers, "authors": authors, "count": len(filtered), "results": filtered}


@app.post("/authors/common")
async def common_records_for_authors(background_tasks: BackgroundTasks, tokens: ApiTokens = Depends(_require_tokens), user_numbers: list[str] = Query(min_length=2)):
    authors, per_author_records = await _records_for_authors(user_numbers, tokens, background_tasks)
    common_ids = {record.get("id") for record in per_author_records[0]}
    for records in per_author_records[1:]:
        common_ids &= {record.get("id") for record in records}
    common = [record for record in _dedupe_records(per_author_records[0]) if record.get("id") in common_ids]
    return {"user_numbers": user_numbers, "authors": authors, "count": len(common), "results": common}
