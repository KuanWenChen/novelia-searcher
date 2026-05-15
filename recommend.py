import json
import os
import time
from collections import defaultdict

from api import NoveliaAPI, extract_novel_links, extract_links_from_comments

CACHE_FILE = "forum_cache.json"
NOVEL_INFO_CACHE_FILE = "novel_info_cache.json"
STATS_CACHE_FILE = "stats_cache.json"
FAIL_CACHE_FILE = "novel_fail_cache.json"


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
                           full_scan: bool = False,
                           on_progress=None,
                           on_article=None,
                           cancel_check=None):
    """漸進式掃描論壇，逐頁交錯：取得列表 → 處理文章 → 取下一頁。

    on_progress(current, total, msg): 進度回報
    on_article(entry, is_cached): 每篇文章處理完後回呼
    cancel_check(): 回傳 True 表示使用者要求中斷

    回傳: list[dict] (所有已處理的文章資料)

    提前結束優化: 文章列表按更新時間排序（最新在前）。若某一頁的
    所有文章快取皆有效，且總頁數與快取記錄一致，代表後續更舊的
    文章也不會有變動，可直接使用快取跳過剩餘頁面。
    """
    cache = {} if force_refresh else load_cache(cache_dir)
    cached_meta = cache.get("_meta", {})
    cached_total_articles = cached_meta.get("total_articles", -1)
    cached_total_pages = cached_meta.get("total_pages", -1)

    page_size = 40
    first_page = api.get_article_list(page=0, page_size=page_size)
    total_pages = first_page["pageNumber"]

    cached_article_count = len(_cache_articles(cache))
    can_early_exit = (not full_scan
                      and cached_article_count == cached_total_articles
                      and total_pages == cached_total_pages)

    list_complete = True
    seen_ids: set[str] = set()
    article_index = 0

    def _process_page(page_articles: list[tuple[str, int]]) -> bool:
        """處理一頁的文章，回傳 False 表示被取消。"""
        nonlocal article_index
        for aid, update_at in page_articles:
            if cancel_check and cancel_check():
                return False
            seen_ids.add(aid)
            article_index += 1

            cached_entry = cache.get(aid)
            if cached_entry and cached_entry.get("updateAt") == update_at:
                if on_progress:
                    on_progress(article_index, -1, f"文章 {article_index}（快取）")
                if on_article:
                    on_article(cached_entry, True)
            else:
                time.sleep(api.scan_interval)
                if on_progress:
                    on_progress(article_index, -1, f"掃描文章 {article_index}")
                article = api.get_article(aid)
                comments = api.get_all_comments(f"article-{aid}")
                entry = {"article": article, "comments": comments,
                         "updateAt": update_at}
                cache[aid] = entry
                cache["_meta"] = {"total_articles": len(_cache_articles(cache)),
                                  "total_pages": total_pages}
                save_cache(cache_dir, cache)
                if on_article:
                    on_article(entry, False)
        return True

    # ── 第 1 頁：取得列表 → 處理文章 ──
    first_articles = [
        (item["id"], item.get("updateAt", 0)) for item in first_page["items"]
    ]

    if can_early_exit and _page_all_cached(first_articles, cache):
        # 第 1 頁全部命中，處理後跳過剩餘頁面
        if on_progress:
            on_progress(total_pages, total_pages,
                        f"文章列表第 1 頁快取命中，跳過剩餘 {total_pages - 1} 頁")
        if not _process_page(first_articles):
            return list(_cache_articles(cache).values())
        # 補上快取中剩餘的文章
        for aid, entry in _cache_articles(cache).items():
            if aid not in seen_ids:
                seen_ids.add(aid)
                article_index += 1
                if on_article:
                    on_article(entry, True)
    else:
        # 處理第 1 頁文章
        if not _process_page(first_articles):
            return list(_cache_articles(cache).values())

        # ── 第 2 頁起：取得列表 → 處理文章 → 下一頁 ──
        for p in range(1, total_pages):
            if cancel_check and cancel_check():
                list_complete = False
                break

            time.sleep(api.scan_interval)
            page_data = api.get_article_list(page=p, page_size=page_size)
            page_articles = [
                (item["id"], item.get("updateAt", 0))
                for item in page_data["items"]
            ]
            if on_progress:
                on_progress(article_index, -1,
                            f"取得文章列表 {p + 1}/{total_pages}")

            # 提前結束檢查
            if can_early_exit and _page_all_cached(page_articles, cache):
                if not _process_page(page_articles):
                    break
                if on_progress:
                    remaining = total_pages - p - 1
                    on_progress(article_index, -1,
                                f"第 {p + 1} 頁快取命中，跳過剩餘 {remaining} 頁")
                # 補上快取中剩餘的文章
                for aid, entry in _cache_articles(cache).items():
                    if aid not in seen_ids:
                        seen_ids.add(aid)
                        article_index += 1
                        if on_article:
                            on_article(entry, True)
                break

            # 處理該頁文章
            if not _process_page(page_articles):
                list_complete = False
                break

    # 清理已刪除的文章（僅在完整掃描時）
    if list_complete:
        for old_id in list(cache.keys()):
            if old_id != "_meta" and old_id not in seen_ids:
                del cache[old_id]

    # 儲存最終狀態
    cache["_meta"] = {"total_articles": len(_cache_articles(cache)),
                      "total_pages": total_pages}
    save_cache(cache_dir, cache)

    return list(_cache_articles(cache).values())


def compute_entry_links(entry: dict) -> dict:
    """從一篇文章提取去重後的小說連結，回傳可序列化的結果。"""
    article = entry["article"]
    comments = entry["comments"]

    article_links = list({
        (p, n) for p, n in extract_novel_links(article.get("content", ""))
    })
    comment_links = list({
        (p, n) for p, n in extract_links_from_comments(comments)
    })

    return {
        "title": article.get("title", ""),
        "article_links": article_links,
        "comment_links": comment_links,
    }


def load_stats_cache(cache_dir: str) -> dict[str, dict]:
    """載入 per-article 統計快取。

    回傳 {article_id: {updateAt, title, article_links, comment_links}}。
    """
    path = os.path.join(cache_dir, STATS_CACHE_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_stats_cache(cache_dir: str, data: dict[str, dict]):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, STATS_CACHE_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_stats_from_article_stats(
    article_stats: dict[str, dict],
) -> dict[tuple[str, str], dict]:
    """從 per-article 連結快取重建聚合統計。"""
    stats: dict[tuple[str, str], dict] = {}
    for aid, adata in article_stats.items():
        article_title = adata.get("title", "")

        seen_in_article: set[tuple[str, str]] = set()
        for p, nid in adata.get("article_links", []):
            key = (p, nid)
            if key not in seen_in_article:
                if key not in stats:
                    stats[key] = {"article_count": 0, "comment_count": 0,
                                  "total_count": 0, "articles": []}
                stats[key]["article_count"] += 1
                stats[key]["articles"].append(article_title)
                seen_in_article.add(key)

        seen_in_comments: set[tuple[str, str]] = set()
        for p, nid in adata.get("comment_links", []):
            key = (p, nid)
            if key not in seen_in_comments:
                if key not in stats:
                    stats[key] = {"article_count": 0, "comment_count": 0,
                                  "total_count": 0, "articles": []}
                stats[key]["comment_count"] += 1
                seen_in_comments.add(key)

        for key in seen_in_article | seen_in_comments:
            stats[key]["total_count"] = (
                stats[key]["article_count"] + stats[key]["comment_count"]
            )
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


def load_fail_cache(cache_dir: str) -> dict[str, int]:
    """載入小說載入失敗次數快取，回傳 {provider/novel_id: fail_count}。"""
    path = os.path.join(cache_dir, FAIL_CACHE_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_fail_cache(cache_dir: str, data: dict[str, int]):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, FAIL_CACHE_FILE)
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
