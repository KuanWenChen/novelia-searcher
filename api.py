import re
import time
import httpx

BASE_URL = "https://n.novelia.cc/api"
NOVELIA_LINK_PATTERN = re.compile(
    r"(?:https?://)?n\.novelia\.cc/novel/(\w+)/([\w-]+)"
)


class NoveliaAPI:
    def __init__(self, token: str, scan_interval: float = 5.0):
        self.scan_interval = scan_interval
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=30.0,
        )

    def close(self):
        self.client.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.client.get(path, params=params)
        if resp.status_code == 401:
            raise PermissionError("Token 已過期或無效，請更新 config.json 中的 token。")
        resp.raise_for_status()
        return resp.json()

    # ── 論壇 ──

    def get_article_list(self, page: int = 0, page_size: int = 20, category: str = "General") -> dict:
        return self._get("/article", params={
            "page": page, "pageSize": page_size, "category": category,
        })

    def get_article(self, article_id: str) -> dict:
        return self._get(f"/article/{article_id}")

    def get_comments(self, site: str, page: int = 0, page_size: int = 50) -> dict:
        return self._get("/comment", params={
            "page": page, "pageSize": page_size, "site": site,
        })

    def get_all_comments(self, site: str, page_size: int = 50) -> list[dict]:
        """取得某個 site 的所有留言（自動翻頁）。"""
        all_items = []
        page = 0
        while True:
            data = self.get_comments(site, page=page, page_size=page_size)
            all_items.extend(data["items"])
            if page + 1 >= data["pageNumber"]:
                break
            page += 1
        return all_items

    # ── 小說 ──

    def search_novels(self, page: int = 0, page_size: int = 20, query: str = "",
                      provider: str = "kakuyomu,syosetu,novelup,hameln,pixiv,alphapolis",
                      novel_type: int = 0, level: int = 0, translate: int = 0, sort: int = 0) -> dict:
        return self._get("/novel", params={
            "page": page, "pageSize": page_size, "query": query,
            "provider": provider, "type": novel_type,
            "level": level, "translate": translate, "sort": sort,
        })

    def get_novel_detail(self, provider: str, novel_id: str) -> dict:
        return self._get(f"/novel/{provider}/{novel_id}")

    def get_novel_comment_count(self, provider: str, novel_id: str) -> int:
        """取得小說的留言總頁數（用第一頁的 pageNumber 推算）。"""
        data = self.get_comments(f"web-{provider}-{novel_id}", page=0, page_size=1)
        return data["pageNumber"]

    # ── 論壇掃描 ──

    def scan_forum(self, on_progress=None):
        """掃描整個 General 論壇，回傳所有文章內容與留言。

        on_progress(current, total, title) 用於回報進度。
        回傳: list[dict]，每個 dict 包含:
            - article: 文章完整資料（含 content）
            - comments: 該文章的所有留言
        """
        first_page = self.get_article_list(page=0, page_size=20)
        total_pages = first_page["pageNumber"]

        all_article_ids = []
        all_article_ids.extend(item["id"] for item in first_page["items"])

        for p in range(1, total_pages):
            time.sleep(self.scan_interval)
            page_data = self.get_article_list(page=p, page_size=20)
            all_article_ids.extend(item["id"] for item in page_data["items"])
            if on_progress:
                on_progress(p + 1, total_pages, f"取得文章列表 {p + 1}/{total_pages}")

        results = []
        total_articles = len(all_article_ids)
        for i, aid in enumerate(all_article_ids):
            time.sleep(self.scan_interval)
            if on_progress:
                on_progress(i + 1, total_articles, f"掃描文章 {i + 1}/{total_articles}")
            article = self.get_article(aid)
            comments = self.get_all_comments(f"article-{aid}")
            results.append({"article": article, "comments": comments})

        return results


def extract_novel_links(text: str) -> list[tuple[str, str]]:
    """從文字中提取所有 novelia 小說連結，回傳 [(provider, novelId), ...]。"""
    return NOVELIA_LINK_PATTERN.findall(text)


def extract_links_from_comments(comments: list[dict]) -> list[tuple[str, str]]:
    """從留言列表（含巢狀回覆）中提取所有小說連結。"""
    links = []
    for comment in comments:
        links.extend(extract_novel_links(comment.get("content", "")))
        if comment.get("replies"):
            links.extend(extract_links_from_comments(comment["replies"]))
    return links
