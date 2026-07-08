"""Generic key/value settings store."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import db


router = APIRouter()


class ValueBody(BaseModel):
    value: str


@router.get("")
async def list_settings():
    async with db.conn.execute("SELECT key, value FROM settings") as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.get("/{key}")
async def get_setting(key: str):
    v = await db.get_setting(key)
    if v is None:
        raise HTTPException(404, "not set")
    return {"key": key, "value": v}


@router.put("/{key}")
async def set_setting(key: str, body: ValueBody):
    await db.set_setting(key, body.value)
    return {"ok": True}
