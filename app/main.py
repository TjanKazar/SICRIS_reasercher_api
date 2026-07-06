from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv("app/.env")

from app.services.auth import get_jwt
from app.services.cobiss import scrape_cobiss_many
from app.services.mongo_cache import get_cached_query, refresh_and_store_query, save_query_document
from app.services.sircis import fetch_author_basic_data, fetch_ids


app = FastAPI(title="COBISS JWT Scraper API")


class AuthBody(BaseModel):
    username: str
    password: str


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
    auth: AuthBody,
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

    token = await get_jwt(auth.username, auth.password)
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
        background_tasks.add_task(refresh_and_store_query, user_number, auth.username, auth.password)

    return author, results


def _records_response(user_number: str, author: dict | None, results: list[dict]):
    return {
        "user_number": user_number,
        "author": author,
        "count": len(results),
        "results": results,
    }


@app.post("/records/{user_number}")
async def get_records(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    limit: int | None = Query(default=None, ge=1),
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks, limit)
        return _records_response(user_number, author, results)

    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/latest")
async def latest_records(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    limit: int = Query(default=20, ge=1, le=500),
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
        return _records_response(user_number, author, _sort_newest(results)[:limit])
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/year/{year}")
async def records_by_year(
    user_number: str,
    year: int,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
        filtered = [record for record in results if _record_year(record) == year]
        return _records_response(user_number, author, filtered)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/years")
async def records_by_year_summary(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
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
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/coauthored")
async def coauthored_records(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
        filtered = [record for record in results if len(record.get("authors", [])) >= 2]
        return _records_response(user_number, author, filtered)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/solo")
async def solo_records(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
        filtered = [record for record in results if len(record.get("authors", [])) <= 1]
        return _records_response(user_number, author, filtered)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/search")
async def search_records(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    q: str = Query(min_length=1),
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
        query = q.casefold()
        filtered = [
            record
            for record in results
            if query in str(record.get("title") or "").casefold()
            or any(query in str(author_name).casefold() for author_name in record.get("authors", []))
        ]
        return _records_response(user_number, author, filtered)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/types")
async def record_type_summary(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
        types: dict[str, int] = {}
        for record in results:
            record_type = _record_type(record) or "unknown"
            types[record_type] = types.get(record_type, 0) + 1

        return {
            "user_number": user_number,
            "author": author,
            "types": dict(sorted(types.items(), key=lambda item: item[1], reverse=True)),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/records/{user_number}/type")
async def records_by_type(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    type_name: str = Query(min_length=1),
):
    try:
        author, results = await _load_author_records(user_number, auth, background_tasks)
        query = type_name.casefold()
        filtered = [
            record
            for record in results
            if query in str(_record_type(record) or "").casefold()
        ]
        return _records_response(user_number, author, filtered)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/authors/unique")
async def unique_records_for_authors(
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    user_numbers: list[str] = Query(min_length=1),
):
    try:
        records: list[dict] = []
        authors: dict[str, dict | None] = {}
        for user_number in user_numbers:
            author, author_records = await _load_author_records(user_number, auth, background_tasks)
            authors[user_number] = author
            records.extend(author_records)

        unique = _dedupe_records(records)
        return {
            "user_numbers": user_numbers,
            "authors": authors,
            "count": len(unique),
            "results": unique,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/authors/collaborations")
async def collaborations_for_authors(
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    user_numbers: list[str] = Query(min_length=1),
):
    try:
        records: list[dict] = []
        authors: dict[str, dict | None] = {}
        for user_number in user_numbers:
            author, author_records = await _load_author_records(user_number, auth, background_tasks)
            authors[user_number] = author
            records.extend(author_records)

        filtered = [record for record in _dedupe_records(records) if len(record.get("authors", [])) >= 2]
        return {
            "user_numbers": user_numbers,
            "authors": authors,
            "count": len(filtered),
            "results": filtered,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/authors/common")
async def common_records_for_authors(
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    user_numbers: list[str] = Query(min_length=2),
):
    try:
        per_author_records: list[list[dict]] = []
        authors: dict[str, dict | None] = {}
        for user_number in user_numbers:
            author, author_records = await _load_author_records(user_number, auth, background_tasks)
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
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
