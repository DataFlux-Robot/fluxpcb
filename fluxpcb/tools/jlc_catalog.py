"""JLC SMT parts catalog client — online verification with cache degradation.

Data source: the JLCPCB assembly parts library (same catalog kicad-jlcpcb-
tools uses). POST selectSmtComponentList by keyword / exact C-code.

Degradation policy (mo9652962 pattern):
  online success  -> refresh disk cache (cache/jlc/<code>.json)
  network failure -> serve from cache
  neither         -> None (caller degrades honestly)

Connection policy: direct connection first (local proxies that break git
push also break API calls); set FLUXNT_PROXY=http://host:port to force one.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "jlc"
_URL = "https://cart.jlcpcb.com/shoppingCart/smtGood/selectSmtComponentList"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://jlcpcb.com/parts",
    "Accept": "application/json, text/plain, */*",
}


def _opener():
    proxy = os.environ.get("FLUXNT_PROXY")
    if proxy:
        handler = urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})   # direct, bypass env proxy
    return urllib.request.build_opener(handler)


def _post(keyword: str, page: int = 1, page_size: int = 20, timeout: float = 15.0):
    data = urllib.parse.urlencode({
        "currentPage": page, "pageSize": page_size,
        "searchSource": "componentLibrary", "keyword": keyword,
    }).encode()
    req = urllib.request.Request(_URL, data=data, headers=_HEADERS, method="POST")
    with _opener().open(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if int(resp.get("code") or 0) != 200:
        # 460/500 = session/bot protection — treat as unreachable, not "absent"
        raise RuntimeError(f"JLC API code={resp.get('code')}: {resp.get('message')}")
    return resp


def _normalize(raw: dict) -> dict:
    """Project a JLC list item onto our catalog schema."""
    prices = raw.get("productPrice") or []
    unit = ""
    if prices:
        best = min(prices, key=lambda p: float(p.get("startNumber") or 1e9))
        unit = str(best.get("cnyPrice") or best.get("usdPrice") or "")
    return {
        "lcsc": raw.get("componentCode", ""),
        "mpn": raw.get("componentModelEn", ""),
        "stock": int(raw.get("stockCount") or 0),
        "price": unit,
        "basic": bool(raw.get("basicPart")),
        "package": raw.get("encapsulation", ""),
        "brand": raw.get("brandNameEn", ""),
        "fetched_at": round(time.time(), 3),
    }


def search(keyword: str, limit: int = 10) -> list[dict] | None:
    """Search by keyword/model. Returns normalized list, or None when the
    network is unavailable and no cache exists for this keyword."""
    try:
        resp = _post(keyword)
        rows = (((resp.get("data") or {}).get("componentPageInfo") or {}).get("list")) or []
        out = [_normalize(r) for r in rows[:limit]]
        _cache_write(f"kw-{_slug(keyword)}", {"query": keyword, "results": out})
        return out
    except Exception:
        cached = _cache_read(f"kw-{_slug(keyword)}")
        return (cached or {}).get("results")


def detail(c_code: str) -> dict | None:
    """Exact C-code lookup. None when unreachable and un-cached."""
    cc = c_code.strip().upper()
    if not re.fullmatch(r"C\d{6,7}", cc):
        return None
    try:
        resp = _post(cc)
        rows = (((resp.get("data") or {}).get("componentPageInfo") or {}).get("list")) or []
        for r in rows:
            if str(r.get("componentCode", "")).upper() == cc:
                norm = _normalize(r)
                _cache_write(cc, norm)
                return norm
        return {"lcsc": cc, "stock": 0, "price": "", "basic": False,
                "mpn": "", "package": "", "brand": "", "not_found": True}
    except Exception:
        return _cache_read(cc)


# ---------------------------------------------------------------- cache
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:80]


def _cache_write(key: str, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _cache_read(key: str) -> dict | None:
    p = CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
