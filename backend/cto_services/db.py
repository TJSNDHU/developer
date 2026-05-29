"""
cto_services/db.py — AUREM Dev
Database handle — set at startup, read by routers.
"""
from fastapi import HTTPException

_db = None


def set_db(database) -> None:
    global _db
    _db = database


def get_db():
    return _db


def require_db():
    if _db is None:
        raise HTTPException(503, "Database not connected")
    return _db
