import json
import os
from collections import defaultdict

from api import NoveliaAPI, extract_novel_links, extract_links_from_comments

CACHE_FILE = "forum_cache.json"


def get_cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, CACHE_FILE)


def load_cache(cache_dir: str) -> list[dict] | None:
    path = get_cache_path(cache_dir)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache_dir: str, data: list[dict]):
    os.makedirs(cache_dir, exist_ok=True)
    path = get_cache_path(cache_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_recommendation_stats(forum_data: list[dict]) -> dict[tuple[str, str], dict]:
    """統計每部小說在論壇中被提及的次數。

    回傳: {(provider, novelId): {
        "article_count": int,
        "comment_count": int,
        "total_count": int,
        "articles": list[str],  # 提到此小說的文章標題
    }}
    """
    stats: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "article_count": 0,
        "comment_count": 0,
        "total_count": 0,
        "articles": [],
    })

    for entry in forum_data:
        article = entry["article"]
        comments = entry["comments"]

        # 掃描文章內容
        article_links = extract_novel_links(article.get("content", ""))
        seen_in_article = set()
        for provider, novel_id in article_links:
            key = (provider, novel_id)
            if key not in seen_in_article:
                stats[key]["article_count"] += 1
                stats[key]["articles"].append(article.get("title", ""))
                seen_in_article.add(key)

        # 掃描留言
        comment_links = extract_links_from_comments(comments)
        seen_in_comments = set()
        for provider, novel_id in comment_links:
            key = (provider, novel_id)
            if key not in seen_in_comments:
                stats[key]["comment_count"] += 1
                seen_in_comments.add(key)

    for key, val in stats.items():
        val["total_count"] = val["article_count"] + val["comment_count"]

    return dict(stats)


def enrich_stats_with_novel_info(api: NoveliaAPI, stats: dict[tuple[str, str], dict],
                                  on_progress=None) -> list[dict]:
    """為每部小說補充標題與留言數。

    回傳排序後的列表: [{
        "provider": str,
        "novel_id": str,
        "title": str,
        "total_count": int,
        "article_count": int,
        "comment_count": int,
        "novel_comment_count": int,
        "link": str,
    }]
    """
    results = []
    items = list(stats.items())
    total = len(items)

    for i, ((provider, novel_id), val) in enumerate(items):
        if on_progress:
            on_progress(i + 1, total, f"取得小說資訊 {i + 1}/{total}")

        title = f"{provider}/{novel_id}"
        novel_comment_count = 0
        try:
            detail = api.get_novel_detail(provider, novel_id)
            title = detail.get("titleZh") or detail.get("titleJp") or title
            # 取得小說留言數
            comment_data = api.get_comments(f"web-{provider}-{novel_id}", page=0, page_size=1)
            novel_comment_count = comment_data.get("pageNumber", 0)
        except Exception:
            pass

        results.append({
            "provider": provider,
            "novel_id": novel_id,
            "title": title,
            "total_count": val["total_count"],
            "article_count": val["article_count"],
            "comment_count": val["comment_count"],
            "novel_comment_count": novel_comment_count,
            "link": f"https://n.novelia.cc/novel/{provider}/{novel_id}",
        })

    results.sort(key=lambda x: x["total_count"], reverse=True)
    return results
