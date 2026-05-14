from api import NoveliaAPI


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


def enrich_comment_count(api: NoveliaAPI, item: dict) -> int:
    """為單筆小說取得留言數，回傳留言數。"""
    try:
        count = api.get_novel_comment_count(item["providerId"], item["novelId"])
    except Exception:
        count = 0
    item["commentCount"] = count
    return count
