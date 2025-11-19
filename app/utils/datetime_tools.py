# app/utils/datetime_tools.py
from __future__ import annotations
from typing import Any, Mapping, Iterable, Union, Set, Callable
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from flask import current_app
from flask_login import current_user

# ---------- helpers ----------

def _active_tz_name() -> str:
    """Per-request timezone: user.timezone if available, else app setting TIMEZONE, else UTC."""
    try:
        if getattr(current_user, "is_authenticated", False) and getattr(current_user, "timezone", None):
            return current_user.timezone or "UTC"
    except Exception:
        pass
    try:
        return current_app.config.get("TIMEZONE", "UTC")
    except Exception:
        return "UTC"

def _to_zoneinfo(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")

def _parse_any_datetime(val: Any) -> datetime | None:
    """Try to parse val into a datetime (aware or naive). Supports:
       - datetime (returns as-is)
       - ISO-like strings: 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM[:SS[.us]]', with or without 'Z' or offset
    """
    if isinstance(val, datetime):
        return val
    if isinstance(val, date) and not isinstance(val, datetime):
        # Treat pure date as midnight
        return datetime(val.year, val.month, val.day)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        # normalize T separator for fromisoformat
        if "T" not in s and " " in s:
            s = s.replace(" ", "T")
        try:
            # Handles date-only and datetime (with or without offset), raises ValueError if not ISO-like
            return datetime.fromisoformat(s)
        except Exception:
            return None
    return None

def _convert_one_for_render(val: datetime, tz_target: ZoneInfo, fmt: str | None) -> Any:
    # Treat naive as UTC (DB convention)
    if val.tzinfo is None:
        val = val.replace(tzinfo=ZoneInfo("UTC"))
    converted = val.astimezone(tz_target)
    return converted.strftime(fmt) if fmt else converted

def _convert_one_to_utc(val: datetime, local_tz: ZoneInfo) -> datetime:
    # If string/naive dt came from user input in local timezone: attach local tz then convert to UTC
    if val.tzinfo is None:
        val = val.replace(tzinfo=local_tz)
    return val.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)  # naive UTC for DB

def _should_convert_key(key: Any, keys: Set[str] | None) -> bool:
    if keys is None:
        return True
    try:
        return str(key) in keys
    except Exception:
        return False

def _walk_transform(
    obj: Any,
    on_datetime: Callable[[datetime], Any],
    parse_strings: bool,
    keys: Set[str] | None,
) -> Any:
    """Deep-copy transform dicts/lists/tuples; apply on_datetime to datetime-like values.
       If parse_strings=True, attempt to parse and convert string date-times too.
       If keys is provided, only convert dict entries whose key name is in keys (lists always converted).
    """
    # dict-like
    if isinstance(obj, Mapping):
        out = {}
        for k, v in obj.items():
            if isinstance(v, (Mapping, list, tuple, set)):
                out[k] = _walk_transform(v, on_datetime, parse_strings, keys)
            else:
                dt = _parse_any_datetime(v) if (parse_strings or isinstance(v, datetime)) else (v if isinstance(v, datetime) else None)
                if dt is not None and _should_convert_key(k, keys):
                    out[k] = on_datetime(dt)
                else:
                    out[k] = v
        return out

    # list/tuple
    if isinstance(obj, list):
        return [_walk_transform(x, on_datetime, parse_strings, keys) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_walk_transform(list(obj), on_datetime, parse_strings, keys))

    # set (rare for payloads)
    if isinstance(obj, set):
        return {_walk_transform(x, on_datetime, parse_strings, keys) for x in obj}

    # leaf
    dt = _parse_any_datetime(obj) if (parse_strings or isinstance(obj, datetime)) else (obj if isinstance(obj, datetime) else None)
    if dt is not None:
        return on_datetime(dt)
    return obj

# ---------- public API ----------

def convert_for_render(
    payload: Any,
    tz_name: str | None = None,
    *,
    fmt: str | None = None,
    parse_strings: bool = False,
    keys: Set[str] | None = None,
) -> Any:
    """
    Convert every datetime in payload FROM UTC TO user's (or given) timezone.
    - payload: dict/list/tuple of primitives and datetimes/strings
    - tz_name: override timezone name (e.g., "America/New_York"); default = active per-request tz
    - fmt: if provided, return formatted strings (e.g., "%Y-%m-%d %H:%M"); else return aware datetimes in target tz
    - parse_strings: also parse and convert string values (ISO-like)
    - keys: if provided, only convert dict entries whose key matches one of these names
    """
    target = _to_zoneinfo(tz_name or _active_tz_name())
    return _walk_transform(
        payload,
        on_datetime=lambda dt: _convert_one_for_render(dt, target, fmt),
        parse_strings=parse_strings,
        keys=keys,
    )

def convert_to_utc(
    payload: Any,
    local_tz_name: str | None = None,
    *,
    parse_strings: bool = True,
    keys: Set[str] | None = None,
) -> Any:
    """
    Convert every datetime in payload FROM user's (or given) timezone TO naive UTC for DB storage.
    - payload: dict/list/tuple of primitives and datetimes/strings
    - local_tz_name: override local timezone to interpret naive inputs; default = active per-request tz
    - parse_strings: parse and convert string values (ISO-like). KEEP THIS True when processing form data.
    - keys: if provided, only convert dict entries whose key matches one of these names
    """
    local = _to_zoneinfo(local_tz_name or _active_tz_name())
    return _walk_transform(
        payload,
        on_datetime=lambda dt: _convert_one_to_utc(dt, local),
        parse_strings=parse_strings,
        keys=keys,
    )