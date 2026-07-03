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

@app.post("/records/{user_number}")
async def get_records(
    user_number: str,
    auth: AuthBody,
    background_tasks: BackgroundTasks,
    limit: int | None = Query(default=None, ge=1),
):
    try:
        print(f"[records] request start user_number={user_number} limit={limit}")
        cached = await get_cached_query(user_number)
        if cached:
            print(f"[records] serving cached response for user_number={user_number}")
            results = cached.get("results", [])
            if limit is not None:
                results = results[:limit]

            return {
                "user_number": user_number,
                "author": cached.get("author"),
                "count": len(results),
                "results": results,
            }

        token = await get_jwt(auth.username, auth.password)
        print(f"[records] cache miss, scraping from source for user_number={user_number}")
        author = await fetch_author_basic_data(user_number, token)
        ids = await fetch_ids(user_number, token)
        scrape_ids = ids[:limit] if limit is not None else ids
        print(f"[records] scraping {len(scrape_ids)} ids for user_number={user_number}")
        results, errors = await scrape_cobiss_many(scrape_ids, token)

        if scrape_ids and not results:
            raise Exception(f"Failed to scrape all COBISS records. Sample error: {errors[0] if errors else 'unknown'}")

        response = {
            "user_number": user_number,
            "author": author,
            "count": len(results),
            "results": results,
        }

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

        return response

    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))