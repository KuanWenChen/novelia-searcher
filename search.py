import json
import os
import time

from api import NoveliaAPI

COMMENT_COUNT_CACHE_FILE = "comment_count_cache.json"
CACHE_TTL = 7 * 24 * 60 * 60  # 7 天


def _load_comment_cache(cache_dir: str) -> dict[str, dict]:
    path = os.path.join(cache_dir, COMMENT_COUNT_CACHE_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_comment_cache(cache_dir: str, data: dict[str, dict]):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, COMMENT_COUNT_CACHE_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def search_novels(
    api: NoveliaAPI,
    page: int = 0,
    page_size: int = 20,
    query: str = "",
    provider: str = "kakuyomu,syosetu,novelup,hameln,pixiv,alphapolis",
    novel_type: int = 0,
    level: int = 0,
    translate: int = 0,
    sort: int = 0,
) -> tuple[list[dict], int]:
    """搜尋小說，回傳原始列表資料（不含留言數）。"""
    data = api.search_novels(
        page=page, page_size=page_size, query=query, provider=provider,
        novel_type=novel_type, level=level, translate=translate, sort=sort,
    )
    items = data["items"]
    for item in items:
        item["commentCount"] = "..."
    return items, data["pageNumber"]


def enrich_comment_count(api: NoveliaAPI, item: dict,
                         cache: dict | None = None) -> int:
    """為單筆小說取得留言數，回傳留言數。支援快取。"""
    key = f"{item['providerId']}/{item['novelId']}"
    now = time.time()

    if cache is not None and key in cache:
        entry = cache[key]
        if now - entry.get("t", 0) < CACHE_TTL:
            item["commentCount"] = entry["c"]
            return entry["c"]

    try:
        count = api.get_novel_comment_count(item["providerId"], item["novelId"])
    except Exception:
        count = 0
    item["commentCount"] = count

    if cache is not None:
        cache[key] = {"c": count, "t": now}

    return count
