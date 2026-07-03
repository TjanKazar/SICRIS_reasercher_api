import httpx

client = httpx.AsyncClient(
    timeout=20,
    headers={"Connection": "close"}
)


async def get(url, **kwargs):
    return await client.get(url, **kwargs)


async def post(url, **kwargs):
    return await client.post(url, **kwargs)