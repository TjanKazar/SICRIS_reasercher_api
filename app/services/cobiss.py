import asyncio

from app.services.http_client import get
from bs4 import BeautifulSoup
import re

BASE_URL = "https://plus.cobiss.net/cobiss/si/sl/data/cobib"


DETAIL_LABELS = {
    "Avtor",
    "Naslov",
    "Jezik",
    "Vrsta gradiva",
    "Tip dela",
    "Leto",
    "Fizični opis",
    "Opombe",
    "Abstract",
    "Vir",
    "Nekontrolirane predmetne oznake",
    "UDK",
    "DOI",
    "Povezava",
    "Način dostopa (URL)",
    "Povzetek",
}

DETAIL_SECTION_END_MARKERS = {
    "Podatki o zapisu",
    "Pogoji uporabe",
    "Politika zasebnosti",
    "Piškotki",
    "Izjava o dostopnosti",
}


def _extract_lines(soup: BeautifulSoup) -> list[str]:
    return [
        line.strip()
        for line in soup.get_text("\n", strip=True).splitlines()
        if line.strip()
    ]


def _extract_cobiss_id(lines: list[str], fallback_id: int) -> int:
    for line in lines:
        match = re.search(r"COBISS-ID:\s*(\d+)", line)
        if match:
            return int(match.group(1))
    return fallback_id


def _extract_detail_data(lines: list[str]) -> dict[str, str]:
    if "Podrobni podatki" not in lines:
        return {}

    start_index = lines.index("Podrobni podatki") + 1
    details: dict[str, str] = {}
    i = start_index

    while i < len(lines):
        line = lines[i]

        if line in DETAIL_SECTION_END_MARKERS:
            break

        if line in DETAIL_LABELS:
            label = line
            i += 1
            value_lines: list[str] = []

            while i < len(lines):
                current = lines[i]
                if current in DETAIL_LABELS or current in DETAIL_SECTION_END_MARKERS:
                    break
                value_lines.append(current)
                i += 1

            details[label] = " ".join(value_lines).strip()
            continue

        i += 1

    return details


async def scrape_cobiss(cobiss_id: int, _token: str):
    url = f"{BASE_URL}/{cobiss_id}"

    # plus.cobiss.net can return malformed Transfer-Encoding headers.
    # Forcing identity encoding avoids httpx RemoteProtocolError.
    r = await get(
        url,
        params={"format": "detail"},
        headers={"Accept-Encoding": "identity"},
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    lines = _extract_lines(soup)
    detail_data = _extract_detail_data(lines)

    title = detail_data.get("Naslov")
    authors_raw = detail_data.get("Avtor")

    return {
        "id": _extract_cobiss_id(lines, cobiss_id),
        "title": title,
        "authors": [author.strip() for author in authors_raw.split(";")] if authors_raw else [],
        "url": f"{url}?format=detail",
        "podrobni_podatki": detail_data,
    }


async def scrape_cobiss_many(cobiss_ids: list[int], token: str, concurrency: int = 5):
    sem = asyncio.Semaphore(concurrency)

    async def safe_scrape(cobiss_id: int):
        async with sem:
            return await scrape_cobiss(cobiss_id, token)

    results = await asyncio.gather(*(safe_scrape(cobiss_id) for cobiss_id in cobiss_ids), return_exceptions=True)

    cleaned: list[dict] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        cleaned.append(result)

    return cleaned, errors