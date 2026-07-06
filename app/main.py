from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv("app/.env")

from app.services.auth import get_jwt
from app.services.cobiss import scrape_cobiss_many
from app.services.mongo_cache import get_cached_query, get_cache_metadata, save_query_document
from app.services.sircis import fetch_author_basic_data, fetch_ids


app = FastAPI(title="COBISS JWT Scraper API")


class AuthBody(BaseModel):
    username: str
    password: str


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Authorization must be a Bearer token")

    return token.strip()


async def _get_request_token(auth: AuthBody | None, authorization: str | None) -> str:
    bearer_token = _extract_bearer_token(authorization)
    if bearer_token:
        return bearer_token

    if auth:
        return await get_jwt(auth.username, auth.password)

    raise HTTPException(status_code=401, detail="Provide Authorization: Bearer <token> or username/password body")


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
    if doi and str(doi).strip():
        return str(doi).strip()
    return None


def _dedupe_records(records: list[dict]) -> list[dict]:
    unique: dict[int | str, dict] = {}
    for record in records:
        unique[record.get("id") or record.get("url") or len(unique)] = record
    return list(unique.values())


def _sort_newest(records: list[dict]) -> list[dict]:
    return sorted(
        records,
        key=lambda record: (_record_year(record) or 0, record.get("id") or 0),
        reverse=True,
    )


async def _load_author_records(
    user_number: str,
    token: str,
    background_tasks: BackgroundTasks,
    limit: int | None = None,
):
    print(f"[records] request start user_number={user_number} limit={limit}")
    cached = await get_cached_query(user_number)
    if cached:
        print(f"[records] serving cached response for user_number={user_number}")
        results = cached.get("results", [])
        if limit is not None:
            results = results[:limit]
        return cached.get("author"), results

    print(f"[records] cache miss, scraping from source for user_number={user_number}")
    author = await fetch_author_basic_data(user_number, token)
    ids = await fetch_ids(user_number, token)
    scrape_ids = ids[:limit] if limit is not None else ids
    print(f"[records] scraping {len(scrape_ids)} ids for user_number={user_number}")
    results, errors = await scrape_cobiss_many(scrape_ids, token)

    if scrape_ids and not results:
        raise Exception(f"Failed to scrape all COBISS records. Sample error: {errors[0] if errors else 'unknown'}")

    if limit is None:
        print(f"[records] scheduling cache save for full response user_number={user_number}")
        background_tasks.add_task(
            save_query_document,
            {
                "user_number": user_number,
                "author": author,
                "results": results,
                "total_count": len(results),
                "complete": not errors,
            },
        )
    else:
        print(f"[records] scheduling background refresh for full cache user_number={user_number}")
        background_tasks.add_task(_refresh_author_records, user_number, token)

    return author, results


async def _refresh_author_records(user_number: str, token: str):
    print(f"[records] force refresh start user_number={user_number}")
    author = await fetch_author_basic_data(user_number, token)
    ids = await fetch_ids(user_number, token)
    print(f"[records] force refresh scraping {len(ids)} ids for user_number={user_number}")
    results, errors = await scrape_cobiss_many(ids, token)

    if ids and not results:
        raise Exception(f"Failed to scrape all COBISS records. Sample error: {errors[0] if errors else 'unknown'}")

    await save_query_document(
        {
            "user_number": user_number,
            "author": author,
            "results": results,
            "total_count": len(results),
            "complete": not errors,
        }
    )
    print(f"[records] force refresh done user_number={user_number}")
    return author, results


def _records_response(user_number: str, author: dict | None, results: list[dict]):
    return {
        "user_number": user_number,
        "author": author,
        "count": len(results),
        "results": results,
    }


@app.post("/auth/token")
async def create_auth_token(auth: AuthBody):
    try:
        token = await get_jwt(auth.username, auth.password)
        return {
            "token_type": "bearer",
            "access_token": token,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}")
async def get_records(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
    limit: int | None = Query(default=None, ge=1),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks, limit)
        return _records_response(user_number, author, results)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/refresh")
async def refresh_records(
    user_number: str,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _refresh_author_records(user_number, token)
        return _records_response(user_number, author, results)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/cache")
async def records_cache_metadata(
    user_number: str,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        await _get_request_token(auth, authorization)
        return await get_cache_metadata(user_number)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/latest")
async def latest_records(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
    limit: int = Query(default=20, ge=1, le=500),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        return _records_response(user_number, author, _sort_newest(results)[:limit])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/year/{year}")
async def records_by_year(
    user_number: str,
    year: int,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        filtered = [record for record in results if _record_year(record) == year]
        return _records_response(user_number, author, filtered)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/years")
async def records_by_year_summary(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        years: dict[int | str, int] = {}
        for record in results:
            year = _record_year(record) or "unknown"
            years[year] = years.get(year, 0) + 1

        ordered_years = {
            year: years[year]
            for year in sorted((year for year in years if isinstance(year, int)), reverse=True)
        }
        if "unknown" in years:
            ordered_years["unknown"] = years["unknown"]

        return {
            "user_number": user_number,
            "author": author,
            "years": ordered_years,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/coauthored")
async def coauthored_records(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        filtered = [record for record in results if len(record.get("authors", [])) >= 2]
        return _records_response(user_number, author, filtered)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/solo")
async def solo_records(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        filtered = [record for record in results if len(record.get("authors", [])) <= 1]
        return _records_response(user_number, author, filtered)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/doi")
async def doi_records(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        filtered = [record for record in results if _record_doi(record)]
        return _records_response(user_number, author, filtered)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/search")
async def search_records(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
    q: str = Query(min_length=1),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        query = q.casefold()
        filtered = [
            record
            for record in results
            if query in str(record.get("title") or "").casefold()
            or any(query in str(author_name).casefold() for author_name in record.get("authors", []))
        ]
        return _records_response(user_number, author, filtered)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/types")
async def record_type_summary(
    user_number: str,
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        token = await _get_request_token(auth, authorization)
        author, results = await _load_author_records(user_number, token, background_tasks)
        types: dict[str, int] = {}
        for record in results:
            record_type = _record_type(record) or "unknown"
            types[record_type] = types.get(record_type, 0) + 1

        return {
            "user_number": user_number,
            "author": author,
            "types": dict(sorted(types.items(), key=lambda item: item[1], reverse=True)),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))



@app.post("/authors/unique")
async def unique_records_for_authors(
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
    user_numbers: list[str] = Query(min_length=1),
):
    try:
        token = await _get_request_token(auth, authorization)
        records: list[dict] = []
        authors: dict[str, dict | None] = {}
        for user_number in user_numbers:
            author, author_records = await _load_author_records(user_number, token, background_tasks)
            authors[user_number] = author
            records.extend(author_records)

        unique = _dedupe_records(records)
        return {
            "user_numbers": user_numbers,
            "authors": authors,
            "count": len(unique),
            "results": unique,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/authors/collaborations")
async def collaborations_for_authors(
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
    user_numbers: list[str] = Query(min_length=1),
):
    try:
        token = await _get_request_token(auth, authorization)
        records: list[dict] = []
        authors: dict[str, dict | None] = {}
        for user_number in user_numbers:
            author, author_records = await _load_author_records(user_number, token, background_tasks)
            authors[user_number] = author
            records.extend(author_records)

        filtered = [record for record in _dedupe_records(records) if len(record.get("authors", [])) >= 2]
        return {
            "user_numbers": user_numbers,
            "authors": authors,
            "count": len(filtered),
            "results": filtered,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/authors/common")
async def common_records_for_authors(
    background_tasks: BackgroundTasks,
    auth: AuthBody | None = None,
    authorization: str | None = Header(default=None),
    user_numbers: list[str] = Query(min_length=2),
):
    try:
        token = await _get_request_token(auth, authorization)
        per_author_records: list[list[dict]] = []
        authors: dict[str, dict | None] = {}
        for user_number in user_numbers:
            author, author_records = await _load_author_records(user_number, token, background_tasks)
            authors[user_number] = author
            per_author_records.append(author_records)

        common_ids = set(record.get("id") for record in per_author_records[0])
        for records in per_author_records[1:]:
            common_ids &= set(record.get("id") for record in records)

        common = [
            record
            for record in _dedupe_records(per_author_records[0])
            if record.get("id") in common_ids
        ]
        return {
            "user_numbers": user_numbers,
            "authors": authors,
            "count": len(common),
            "results": common,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
