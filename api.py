import re
import httpx

BASE_URL = "https://n.novelia.cc/api"
NOVELIA_LINK_PATTERN = re.compile(
    r"(?:https?://)?n\.novelia\.cc/novel/([A-Za-z0-9_]+)/([A-Za-z0-9_-]+)"
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
