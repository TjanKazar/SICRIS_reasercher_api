from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncio

from app.services.auth import get_jwt
from app.services.sircis import fetch_ids
from app.services.cobiss import scrape_cobiss


app = FastAPI(title="COBISS JWT Scraper API")


class AuthBody(BaseModel):
    username: str
    password: str

@app.post("/records/{user_number}")
async def get_records(user_number: str, auth: AuthBody):
    try:
        token = await get_jwt(auth.username, auth.password)

        ids = await fetch_ids(user_number, token)

        sem = asyncio.Semaphore(5)

        async def safe_scrape(cobiss_id: int):
            async with sem:
                return await scrape_cobiss(cobiss_id, token)

        tasks = [safe_scrape(i) for i in ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        cleaned = []
        errors = []
        for r in results:
            if isinstance(r, Exception):
                errors.append(str(r))
                continue
            cleaned.append(r)

        if ids and not cleaned:
            raise Exception(f"Failed to scrape all COBISS records. Sample error: {errors[0] if errors else 'unknown'}")

        return {
            "user_number": user_number,
            "count": len(cleaned),
            "results": cleaned,
        }

    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))