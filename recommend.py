import json
import os
import time
from collections import defaultdict

from api import NoveliaAPI, extract_novel_links, extract_links_from_comments

CACHE_FILE = "forum_cache.json"
NOVEL_INFO_CACHE_FILE = "novel_info_cache.json"


def get_cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, CACHE_FILE)


def load_cache(cache_dir: str) -> dict[str, dict]:
    """載入快取，回傳 {article_id: {article, comments, updateAt}}。"""
    path = get_cache_path(cache_dir)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 相容舊格式（list）→ 轉換為新格式（dict）
    if isinstance(data, list):
        return {entry["article"]["id"]: entry for entry in data}
    return data


def save_cache(cache_dir: str, data: dict[str, dict]):
    os.makedirs(cache_dir, exist_ok=True)
    path = get_cache_path(cache_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _cache_articles(cache: dict[str, dict]) -> dict[str, dict]:
    """回傳 cache 中的文章資料（排除 _meta）。"""
    return {k: v for k, v in cache.items() if k != "_meta"}


def _page_all_cached(page_articles: list[tuple[str, int]], cache: dict[str, dict]) -> bool:
    """檢查一頁的所有文章是否都在快取中且 updateAt 一致。"""
    return all(
        aid in cache and cache[aid].get("updateAt") == upd
        for aid, upd in page_articles
    )


def scan_forum_incremental(api: NoveliaAPI, cache_dir: str,
                           force_refresh: bool = False,
                           on_progress=None,
                           on_article=None,
                           cancel_check=None):
    """漸進式掃描論壇，逐篇寫入快取。

    on_progress(current, total, msg): 進度回報
    on_article(entry, is_cached): 每篇文章處理完後回呼
    cancel_check(): 回傳 True 表示使用者要求中斷

    回傳: list[dict] (所有已處理的文章資料)

    Phase 1 優化: 文章列表按更新時間排序（最新在前）。若某一頁的
    所有文章快取皆有效，且總頁數與快取記錄一致，代表後續更舊的
    文章也不會有變動，可直接使用快取跳過剩餘頁面。
    """
    cache = {} if force_refresh else load_cache(cache_dir)
    cached_total_pages = cache.get("_meta", {}).get("total_pages", -1)

    # Phase 1: 取得文章列表（含提前結束優化）
    first_page = api.get_article_list(page=0, page_size=40)
    total_pages = first_page["pageNumber"]

    article_list: list[tuple[str, int]] = [
        (item["id"], item.get("updateAt", 0)) for item in first_page["items"]
    ]

    list_complete = True  # 是否完整取得了所有文章列表
    early_exit = False

    # 檢查第一頁是否可以提前結束
    if total_pages == cached_total_pages and _page_all_cached(article_list, cache):
        early_exit = True
        if on_progress:
            on_progress(total_pages, total_pages,
                        f"文章列表第 1 頁快取命中，跳過剩餘 {total_pages - 1} 頁")
    else:
        for p in range(1, total_pages):
            if cancel_check and cancel_check():
                list_complete = False
                break
            time.sleep(api.scan_interval)
            page_data = api.get_article_list(page=p, page_size=40)
            page_articles = [(item["id"], item.get("updateAt", 0)) for item in page_data["items"]]
            article_list.extend(page_articles)
            if on_progress:
                on_progress(p + 1, total_pages, f"取得文章列表 {p + 1}/{total_pages}")

            # 若此頁全部快取命中且總頁數一致，後續頁面可跳過
            if total_pages == cached_total_pages and _page_all_cached(page_articles, cache):
                early_exit = True
                if on_progress:
                    remaining = total_pages - p - 1
                    on_progress(total_pages, total_pages,
                                f"文章列表第 {p + 1} 頁快取命中，跳過剩餘 {remaining} 頁")
                break

    if cancel_check and cancel_check():
        return list(_cache_articles(cache).values())

    if early_exit:
        # 把快取中尚未出現在 article_list 的文章補上
        seen_ids = {aid for aid, _ in article_list}
        for aid, entry in _cache_articles(cache).items():
            if aid not in seen_ids:
                article_list.append((aid, entry.get("updateAt", 0)))

    # 只有在完整取得文章列表時才清理已刪除的文章
    if list_complete:
        current_ids = {aid for aid, _ in article_list}
        for old_id in list(cache.keys()):
            if old_id != "_meta" and old_id not in current_ids:
                del cache[old_id]

    # Phase 2: 逐篇處理（快取命中則跳過 API 呼叫）
    total = len(article_list)
    for i, (aid, update_at) in enumerate(article_list):
        if cancel_check and cancel_check():
            break

        cached_entry = cache.get(aid)
        if cached_entry and cached_entry.get("updateAt") == update_at:
            # 快取命中，不需重新抓取
            if on_progress:
                on_progress(i + 1, total, f"文章 {i + 1}/{total}（快取）")
            if on_article:
                on_article(cached_entry, True)
        else:
            # 需要抓取（新文章或已更新）
            time.sleep(api.scan_interval)
            if on_progress:
                on_progress(i + 1, total, f"掃描文章 {i + 1}/{total}")
            article = api.get_article(aid)
            comments = api.get_all_comments(f"article-{aid}")
            entry = {"article": article, "comments": comments, "updateAt": update_at}
            cache[aid] = entry
            cache["_meta"] = {"total_pages": total_pages}
            save_cache(cache_dir, cache)
            if on_article:
                on_article(entry, False)

    # 儲存最終狀態（含 metadata）
    cache["_meta"] = {"total_pages": total_pages}
    save_cache(cache_dir, cache)

    return list(_cache_articles(cache).values())


def add_entry_to_stats(entry: dict, stats: dict[tuple[str, str], dict]):
    """將一篇文章的資料加入推薦統計。"""
    article = entry["article"]
    comments = entry["comments"]

    article_links = extract_novel_links(article.get("content", ""))
    seen_in_article: set[tuple[str, str]] = set()
    for provider, novel_id in article_links:
        key = (provider, novel_id)
        if key not in seen_in_article:
            if key not in stats:
                stats[key] = {"article_count": 0, "comment_count": 0, "total_count": 0, "articles": []}
            stats[key]["article_count"] += 1
            stats[key]["articles"].append(article.get("title", ""))
            seen_in_article.add(key)

    comment_links = extract_links_from_comments(comments)
    seen_in_comments: set[tuple[str, str]] = set()
    for provider, novel_id in comment_links:
        key = (provider, novel_id)
        if key not in seen_in_comments:
            if key not in stats:
                stats[key] = {"article_count": 0, "comment_count": 0, "total_count": 0, "articles": []}
            stats[key]["comment_count"] += 1
            seen_in_comments.add(key)

    for key in seen_in_article | seen_in_comments:
        stats[key]["total_count"] = stats[key]["article_count"] + stats[key]["comment_count"]


def build_recommendation_stats(forum_data: list[dict]) -> dict[tuple[str, str], dict]:
    """統計每部小說在論壇中被提及的次數。

    回傳: {(provider, novelId): {
        "article_count": int,
        "comment_count": int,
        "total_count": int,
        "articles": list[str],  # 提到此小說的文章標題
    }}
    """
    stats: dict[tuple[str, str], dict] = {}
    for entry in forum_data:
        add_entry_to_stats(entry, stats)
    return stats


def load_novel_info_cache(cache_dir: str) -> dict[str, dict]:
    """載入小說資訊快取，回傳 {provider/novel_id: {title, keywords, novel_comment_count}}。"""
    path = os.path.join(cache_dir, NOVEL_INFO_CACHE_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_novel_info_cache(cache_dir: str, data: dict[str, dict]):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, NOVEL_INFO_CACHE_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def prepare_recommend_data(stats: dict[tuple[str, str], dict],
                           cache_dir: str) -> list[dict]:
    """從統計資料建立推薦列表，已快取的小說資訊直接套用。

    回傳排序後的列表，尚未取得資訊的項目 enriched=False。
    """
    novel_cache = load_novel_info_cache(cache_dir)
    results = []
    for (provider, novel_id), val in stats.items():
        key = f"{provider}/{novel_id}"
        cached = novel_cache.get(key)
        if cached:
            title = cached.get("title", key)
            keywords = cached.get("keywords", [])
            novel_comment_count = cached.get("novel_comment_count", 0)
            attentions = cached.get("attentions", [])
            enriched = True
        else:
            title = key
            keywords = []
            novel_comment_count = 0
            attentions = []
            enriched = False

        results.append({
            "provider": provider,
            "novel_id": novel_id,
            "title": title,
            "total_count": val["total_count"],
            "article_count": val["article_count"],
            "comment_count": val["comment_count"],
            "novel_comment_count": novel_comment_count,
            "link": f"https://n.novelia.cc/novel/{provider}/{novel_id}",
            "keywords": keywords,
            "attentions": attentions,
            "enriched": enriched,
        })

    results.sort(key=lambda x: x["total_count"], reverse=True)
    return results


def fetch_novel_info(api: NoveliaAPI, provider: str, novel_id: str) -> dict | None:
    """從 API 取得單部小說的標題、標籤、留言數。失敗回傳 None。"""
    try:
        detail = api.get_novel_detail(provider, novel_id)
        title = detail.get("titleZh") or detail.get("titleJp") or f"{provider}/{novel_id}"
        keywords = detail.get("keywords") or []
        comment_data = api.get_comments(f"web-{provider}-{novel_id}", page=0, page_size=1)
        novel_comment_count = comment_data.get("pageNumber", 0)
        return {
            "title": title,
            "keywords": keywords,
            "novel_comment_count": novel_comment_count,
            "attentions": detail.get("attentions") or [],
        }
    except Exception:
        return None
