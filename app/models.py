from pydantic import BaseModel


class CobissRecord(BaseModel):
    id: int
    url: str
    title: str | None = None