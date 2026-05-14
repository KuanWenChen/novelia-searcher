import json
import logging
import os
import subprocess
import threading
import time as _time
import unicodedata
import webbrowser

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal, VerticalScroll, Center
from textual.widgets import (
    Header, Footer, DataTable, Input, Static, Label,
    Button, Select, SelectionList, OptionList,
)
from textual.widgets.option_list import Option
from textual.screen import Screen
from textual.reactive import reactive

from api import NoveliaAPI

_s2twp = None
_s2twp_lock = threading.Lock()


def _get_s2twp():
    global _s2twp
    if _s2twp is None:
        with _s2twp_lock:
            if _s2twp is None:
                import opencc
                _s2twp = opencc.OpenCC("s2twp")
    return _s2twp


def _init_s2twp():
    """在背景執行緒預先載入 opencc，避免首次使用時卡頓。"""
    threading.Thread(target=_get_s2twp, daemon=True).start()

log = logging.getLogger("ui")



# cmd.exe 下 Ambiguous 寬度字元顯示為 2 格，但 Rich 算 1 格。
# 替換成對應的全形字元讓 Rich 的計算與終端一致。
CHAR_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "char_map.json")
_char_map: dict[str, str] = {}


def load_char_map():
    """從 char_map.json 載入字元替換表。"""
    global _char_map
    try:
        with open(CHAR_MAP_PATH, "r", encoding="utf-8") as f:
            _char_map = json.load(f)
        log.info(f"載入 char_map.json，共 {len(_char_map)} 筆")
    except Exception as e:
        log.warning(f"載入 char_map.json 失敗: {e}")
        _char_map = {}


load_char_map()


def normalize_for_table(s: str) -> str:
    """替換 Ambiguous 寬度字元，讓 Rich 計算與 cmd.exe 顯示一致。"""
    for old, new in _char_map.items():
        s = s.replace(old, new)
    return s


def truncate_to_width(s: str, max_width: int) -> str:
    """按 Rich 顯示寬度截斷字串，超過加 '...'。"""
    from rich.cells import cell_len
    if cell_len(s) <= max_width:
        return s
    for i in range(len(s), 0, -1):
        candidate = s[:i] + "..."
        if cell_len(candidate) <= max_width:
            return candidate
    return "..."


# ── 詳情頁 ──

class NovelDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "返回"),
    ]

    def __init__(self, api: NoveliaAPI, provider: str, novel_id: str):
        super().__init__()
        self.api = api
        self.provider = provider
        self.novel_id = novel_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(Static(id="detail-content", markup=False))
        yield Footer()

    def on_mount(self):
        self._load_detail()

    def _load_detail(self):
        content_widget = self.query_one("#detail-content", Static)
        try:
            detail = self.api.get_novel_detail(self.provider, self.novel_id)
        except PermissionError as e:
            content_widget.update(str(e))
            return
        except Exception as e:
            content_widget.update(f"載入失敗: {e}")
            return

        title_zh = detail.get("titleZh") or ""
        title_jp = detail.get("titleJp") or ""
        intro_zh = detail.get("introductionZh") or ""
        intro_jp = detail.get("introductionJp") or ""
        visited = detail.get("visited", 0)
        points = detail.get("points", 0)
        total_chars = detail.get("totalCharacters", 0)
        keywords = ", ".join(detail.get("keywords", []))
        attentions = ", ".join(detail.get("attentions", []))

        jp = detail.get("jp", 0)
        baidu = detail.get("baidu", 0)
        youdao = detail.get("youdao", 0)
        gpt = detail.get("gpt", 0)
        sakura = detail.get("sakura", 0)

        authors = ", ".join(a.get("name", "") for a in detail.get("authors", []))

        lines = [
            f"{'─' * 60}",
            f"  標題（中文）: {title_zh}",
            f"  標題（日文）: {title_jp}",
            f"  作者: {authors}",
            f"  連結: https://n.novelia.cc/novel/{self.provider}/{self.novel_id}",
            f"{'─' * 60}",
            f"  瀏覽數: {visited}    評分: {points}    總字數: {total_chars}",
            f"  翻譯狀態: 原文 {jp} / 百度 {baidu} / 有道 {youdao} / GPT {gpt} / Sakura {sakura}",
            f"{'─' * 60}",
        ]

        if attentions:
            lines.append(f"  注意事項: {attentions}")
        if keywords:
            lines.append(f"  關鍵字: {keywords}")

        lines.append(f"{'─' * 60}")
        lines.append("  簡介（中文）:")
        lines.append(f"  {intro_zh}" if intro_zh else "  （無）")
        lines.append("")
        lines.append("  簡介（日文）:")
        lines.append(f"  {intro_jp}" if intro_jp else "  （無）")
        lines.append(f"{'─' * 60}")

        # 載入留言
        lines.append("")
        lines.append("  留言:")
        lines.append("")
        try:
            comments_data = self.api.get_comments(
                f"web-{self.provider}-{self.novel_id}", page=0, page_size=20
            )
            comments = comments_data.get("items", [])
            total_comment_pages = comments_data.get("pageNumber", 0)
            if not comments:
                lines.append("  （無留言）")
            else:
                for c in comments:
                    user = c.get("user", {}).get("username", "匿名")
                    text = c.get("content", "").strip()
                    lines.append(f"  [{user}]: {text}")
                    for reply in c.get("replies", []):
                        r_user = reply.get("user", {}).get("username", "匿名")
                        r_text = reply.get("content", "").strip()
                        lines.append(f"    └ [{r_user}]: {r_text}")
                    lines.append("")
                if total_comment_pages > 1:
                    lines.append(f"  （共 {total_comment_pages} 頁留言，僅顯示第 1 頁）")
        except Exception as e:
            lines.append(f"  載入留言失敗: {e}")

        content_widget.update("\n".join(lines))

    def action_go_back(self):
        self.app.pop_screen()


# ── 列表頁（通用） ──

class NovelListScreen(Screen):
    BINDINGS = [
        Binding("slash", "start_filter", "搜尋過濾"),
        Binding("escape", "cancel_filter_or_back", "取消/返回"),
        Binding("left", "prev_page", "上一頁"),
        Binding("right", "next_page", "下一頁"),
        Binding("z", "prev_page", "上一頁(z)"),
        Binding("x", "next_page", "下一頁(x)"),
        Binding("space", "toggle_mark", "標記"),
        Binding("o", "open_in_browser", "瀏覽器開啟"),
        Binding("c", "copy_title", "複製標題"),
        Binding("s", "cycle_sort", "切換排序"),
        Binding("r", "reload_char_map", "重載字元表"),
    ]

    current_page = reactive(0)
    filter_text = reactive("")

    SORT_MODES = [
        ("預設", None),
        ("更新時間", "updateAt"),
        ("留言數", "commentCount"),
    ]

    def __init__(self, api: NoveliaAPI, title: str = "小說列表"):
        super().__init__()
        self.api = api
        self.screen_title = title
        self.items: list[dict] = []
        self._items_original: list[dict] = []
        self.total_pages: int = 0
        self.marked: set[int] = set()
        self._filtering = False
        self._sort_index = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Label("", id="status-bar")
            yield Input(placeholder="輸入關鍵字過濾...", id="filter-input")
            yield DataTable(id="novel-table")
        yield Footer()

    def on_mount(self):
        self.query_one("#filter-input", Input).display = False
        table = self.query_one("#novel-table", DataTable)
        table.cursor_type = "row"
        self._setup_columns(table)
        self._load_data()

    def _setup_columns(self, table: DataTable):
        table.add_columns(" ", "標題", "資訊")

    def _load_data(self):
        pass

    def _get_display_title(self, item: dict) -> str:
        title = item.get("titleZh") or item.get("titleJp") or item.get("title", "")
        title = " ".join(title.split())
        return normalize_for_table(_get_s2twp().convert(title))

    def _is_r18(self, item: dict) -> bool:
        attentions = item.get("attentions", [])
        for a in attentions:
            if "R18" in a or "r18" in a.lower():
                return True
        return False

    def _refresh_table(self):
        table = self.query_one("#novel-table", DataTable)
        prev_cursor = table.cursor_row if table.row_count > 0 else 0
        table.clear()
        filter_lower = self.filter_text.lower()
        for i, item in enumerate(self.items):
            title = self._get_display_title(item)
            if filter_lower and filter_lower not in title.lower():
                continue
            mark = "*" if i in self.marked else " "
            row_data = self._get_row_data(item)
            if self._is_r18(item):
                row_data = tuple(
                    Text(str(cell), style="bold magenta") for cell in row_data
                )
                mark = Text(mark, style="bold magenta")
            table.add_row(mark, *row_data, key=str(i))
        if table.row_count > 0:
            table.move_cursor(row=min(prev_cursor, table.row_count - 1))
        self._update_status()

    def _get_row_data(self, item: dict) -> tuple:
        title = self._get_display_title(item)
        return (title,)

    def _apply_sort(self):
        """根據目前排序模式排序 items。"""
        sort_name, sort_key = self.SORT_MODES[self._sort_index]
        if sort_key is None:
            self.items = list(self._items_original)
        else:
            def _sort_val(x):
                v = x.get(sort_key, 0)
                return v if isinstance(v, (int, float)) else 0
            self.items = sorted(
                self._items_original,
                key=_sort_val,
                reverse=True,
            )

    def _update_status(self):
        sort_name = self.SORT_MODES[self._sort_index][0]
        status = self.query_one("#status-bar", Label)
        status.update(
            f"  第 {self.current_page + 1} 頁 / 共 {self.total_pages} 頁"
            f"    共 {len(self.items)} 筆"
            f"    排序: {sort_name}"
        )

    def _get_selected_item(self) -> dict | None:
        table = self.query_one("#novel-table", DataTable)
        if table.row_count == 0:
            return None
        cursor_key = list(table.rows.keys())[table.cursor_row]
        idx = int(cursor_key.value)
        if 0 <= idx < len(self.items):
            return self.items[idx]
        return None

    def _get_novel_url(self, item: dict) -> str | None:
        provider = item.get("providerId") or item.get("provider", "")
        novel_id = item.get("novelId") or item.get("novel_id", "")
        if provider and novel_id:
            return f"https://n.novelia.cc/novel/{provider}/{novel_id}"
        return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        """Enter / 滑鼠雙擊：開啟詳情頁"""
        item = self._get_selected_item()
        if item:
            provider = item.get("providerId") or item.get("provider", "")
            novel_id = item.get("novelId") or item.get("novel_id", "")
            if provider and novel_id:
                self.app.push_screen(NovelDetailScreen(self.api, provider, novel_id))

    def action_open_in_browser(self):
        """按 o：用瀏覽器開啟"""
        item = self._get_selected_item()
        if item:
            url = self._get_novel_url(item)
            if url:
                webbrowser.open(url)

    def action_copy_title(self):
        """按 c：複製標題到剪貼簿"""
        item = self._get_selected_item()
        if item:
            title = self._get_display_title(item)
            if title:
                subprocess.run(
                    ["clip"], input=title.encode("utf-16le"),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.notify(f"已複製: {title}", timeout=2)

    def action_start_filter(self):
        self._filtering = True
        filter_input = self.query_one("#filter-input", Input)
        filter_input.display = True
        filter_input.focus()

    def action_cancel_filter_or_back(self):
        if self._filtering:
            self._filtering = False
            filter_input = self.query_one("#filter-input", Input)
            filter_input.display = False
            filter_input.value = ""
            self.filter_text = ""
            self._refresh_table()
            self.query_one("#novel-table", DataTable).focus()
        else:
            self.app.pop_screen()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "filter-input":
            self.filter_text = event.value
            self._refresh_table()
            self._filtering = False
            event.input.display = False
            self.query_one("#novel-table", DataTable).focus()

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "filter-input":
            self.filter_text = event.value
            self._refresh_table()

    def action_prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._load_data()

    def action_next_page(self):
        if self.current_page + 1 < self.total_pages:
            self.current_page += 1
            self._load_data()

    def action_toggle_mark(self):
        table = self.query_one("#novel-table", DataTable)
        if table.row_count == 0:
            return
        cursor_key = list(table.rows.keys())[table.cursor_row]
        idx = int(cursor_key.value)
        if idx in self.marked:
            self.marked.discard(idx)
        else:
            self.marked.add(idx)
        self._refresh_table()

    def action_reload_char_map(self):
        load_char_map()
        self._refresh_table()
        status = self.query_one("#status-bar", Label)
        status.update(f"  字元替換表已重載（{len(_char_map)} 筆）")

    def action_cycle_sort(self):
        self._sort_index = (self._sort_index + 1) % len(self.SORT_MODES)
        self._apply_sort()
        self._refresh_table()




# ── 搜尋參數設定頁 ──

class SearchFormScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "返回"),
    ]

    def __init__(self, api: NoveliaAPI):
        super().__init__()
        self.api = api

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("  搜尋條件設定", classes="form-title")
            yield Static("")

            yield Label("  關鍵字:")
            yield Input(placeholder="輸入搜尋關鍵字（可留空）", id="query")

            yield Label("  來源平台（Space 切換選取）:")
            yield SelectionList(
                ("Syosetu", "syosetu", True),
                ("Kakuyomu", "kakuyomu", True),
                ("NovelUp", "novelup", True),
                ("Hameln", "hameln", True),
                ("Pixiv", "pixiv", False),
                ("Alphapolis", "alphapolis", True),
                id="provider",
            )

            with Horizontal(classes="form-row"):
                with Vertical(classes="form-field"):
                    yield Label("  類型:")
                    yield Select(
                        [("全部", 0), ("連載中", 1), ("已完結", 2), ("短篇", 3)],
                        value=0, id="novel-type",
                    )
                with Vertical(classes="form-field"):
                    yield Label("  分級:")
                    yield Select(
                        [("全部", 0), ("一般向", 1), ("R18", 2)],
                        value=0, id="level",
                    )

            with Horizontal(classes="form-row"):
                with Vertical(classes="form-field"):
                    yield Label("  翻譯:")
                    yield Select(
                        [("全部", 0), ("GPT", 1), ("Sakura", 2)],
                        value=0, id="translate",
                    )
                with Vertical(classes="form-field"):
                    yield Label("  排序:")
                    yield Select(
                        [("更新", 0), ("點擊", 1), ("相關", 2)],
                        value=0, id="sort",
                    )

            yield Static("")
            yield Label("  進階篩選（留空表示不限）:")

            with Horizontal(classes="form-row"):
                with Vertical(classes="form-field"):
                    yield Label("  最低留言數:")
                    yield Input(placeholder="例: 10", id="min-comments")
                with Vertical(classes="form-field"):
                    yield Label("  每頁筆數:")
                    yield Input(value="20", id="page-size")

            yield Static("")
            with Center():
                yield Button("開始搜尋", variant="primary", id="btn-search")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-search":
            self._do_search()

    def _parse_int(self, input_id: str) -> int | None:
        val = self.query_one(f"#{input_id}", Input).value.strip()
        if not val:
            return None
        try:
            return int(val)
        except ValueError:
            return None

    def _do_search(self):
        params = {
            "query": self.query_one("#query", Input).value.strip(),
            "provider": ",".join(self.query_one("#provider", SelectionList).selected),
            "novel_type": self.query_one("#novel-type", Select).value,
            "level": self.query_one("#level", Select).value,
            "translate": self.query_one("#translate", Select).value,
            "sort": self.query_one("#sort", Select).value,
        }

        min_c = self._parse_int("min-comments")
        page_size = self._parse_int("page-size") or 20
        if min_c is not None:
            params["min_comments"] = min_c
        params["page_size"] = page_size

        self.app.push_screen(SearchScreen(self.api, params))

    def action_go_back(self):
        self.app.pop_screen()


# ── 搜尋結果列表頁 ──

class SearchScreen(NovelListScreen):
    def __init__(self, api: NoveliaAPI, search_params: dict):
        super().__init__(api, title="小說搜尋")
        self.min_comments = search_params.pop("min_comments", None)
        self.search_params = search_params

    def _setup_columns(self, table: DataTable):
        table.add_columns(" ", "標題", "章數", "留言", "更新時間", "標籤")

    def _load_data(self):
        status = self.query_one("#status-bar", Label)
        status.update("  載入中...")
        self.run_worker(self._load_list_thread, thread=True)

    def _load_list_thread(self):
        """第一階段：取得列表，立即顯示。"""
        from search import search_novels
        try:
            items, total_pages = search_novels(
                self.api,
                page=self.current_page,
                **self.search_params,
            )
            self.app.call_from_thread(self._on_list_ready, items, total_pages)
        except PermissionError as e:
            self.app.call_from_thread(
                self.query_one("#status-bar", Label).update, f"  錯誤: {e}"
            )
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#status-bar", Label).update, f"  錯誤: {e}"
            )

    def _on_list_ready(self, items, total_pages):
        """列表到手，先顯示，再背景補留言數。"""
        self._items_original = list(items)
        self.total_pages = total_pages
        self._apply_sort()
        self._refresh_table()
        self.run_worker(self._enrich_thread, thread=True)

    def _enrich_thread(self):
        """第二階段：背景逐筆補留言數。"""
        from search import enrich_comment_count
        items = self._items_original
        total = len(items)
        for i, item in enumerate(items):
            if item.get("commentCount") != "...":
                continue
            enrich_comment_count(self.api, item)
            # 留言數篩選
            if self.min_comments is not None and item["commentCount"] < self.min_comments:
                item["_hidden"] = True
            self.app.call_from_thread(self._on_enrich_update, i + 1, total)

    def _on_enrich_update(self, current, total):
        # 過濾掉被隱藏的項目
        self._items_original = [i for i in self._items_original if not i.get("_hidden")]
        self._apply_sort()
        self._refresh_table()
        status = self.query_one("#status-bar", Label)
        sort_name = self.SORT_MODES[self._sort_index][0]
        if current < total:
            status.update(
                f"  第 {self.current_page + 1} 頁 / 共 {self.total_pages} 頁"
                f"    共 {len(self.items)} 筆"
                f"    排序: {sort_name}"
                f"    留言載入中 {current}/{total}"
            )

    TITLE_WIDTH = 140
    KEYWORDS_WIDTH = 50

    def _get_row_data(self, item: dict) -> tuple:
        title = self._get_display_title(item)
        title = truncate_to_width(title, self.TITLE_WIDTH)
        total = item.get("total", 0)
        comments = item.get("commentCount", 0)
        update_at = item.get("updateAt", 0)
        date_str = _time.strftime("%Y-%m-%d", _time.localtime(update_at)) if update_at else ""
        keywords = ", ".join(item.get("keywords", []))
        keywords = truncate_to_width(normalize_for_table(keywords), self.KEYWORDS_WIDTH)
        return (title, str(total), str(comments), date_str, keywords)


# ── 推薦統計載入頁 ──

from textual.message import Message


class RecommendLoadingScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "返回 / 中斷掃描"),
    ]

    class RecommendReady(Message):
        """推薦資料載入完成。"""
        def __init__(self, data: list[dict], api: NoveliaAPI, cache_dir: str):
            super().__init__()
            self.data = data
            self.api = api
            self.cache_dir = cache_dir

    def __init__(self, api: NoveliaAPI, cache_dir: str):
        super().__init__()
        self.api = api
        self.cache_dir = cache_dir
        self._cancel_event = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("  正在載入推薦統計...\n", id="loading-status", markup=False)
        yield Footer()

    def on_mount(self):
        self.run_worker(self._load_recommend, thread=True)

    def _update_status(self, text: str):
        self.app.call_from_thread(self._set_status, text)

    def _set_status(self, text: str):
        self.query_one("#loading-status", Static).update(text)

    def _load_recommend(self):
        from recommend import (scan_forum_incremental, add_entry_to_stats,
                               prepare_recommend_data)
        import traceback

        lines: list[str] = []
        stats_line_start = -1
        stats: dict[tuple[str, str], dict] = {}
        article_count = 0

        def log_msg(msg: str):
            lines.append(msg)
            self._update_status("\n".join(lines))

        def update_progress_line(msg: str, prefix: str):
            for i, line in enumerate(lines):
                if line.startswith(prefix):
                    lines[i] = f"{prefix}{msg}"
                    self._update_status("\n".join(lines))
                    return
            lines.append(f"{prefix}{msg}")
            self._update_status("\n".join(lines))

        def update_stats_display():
            nonlocal stats_line_start
            if stats_line_start >= 0:
                del lines[stats_line_start:]
            else:
                stats_line_start = len(lines)

            if not stats:
                return

            sorted_stats = sorted(stats.items(), key=lambda x: x[1]["total_count"], reverse=True)
            lines.append(f"\n  目前已處理 {article_count} 篇文章，找到 {len(sorted_stats)} 部被提及的小說")
            lines.append("  ─" * 30)
            for rank, ((provider, novel_id), val) in enumerate(sorted_stats, 1):
                nid = novel_id if len(novel_id) <= 20 else novel_id[:17] + "..."
                lines.append(
                    f"  {rank:>2}. {provider}/{nid}"
                    f"  — 提到 {val['total_count']} 次"
                    f"（文章 {val['article_count']} + 留言 {val['comment_count']}）"
                )
            self._update_status("\n".join(lines))

        def on_scan_progress(current, total, msg):
            update_progress_line(msg, "  掃描進度: ")

        def on_article(entry, is_cached):
            nonlocal article_count
            article_count += 1
            add_entry_to_stats(entry, stats)
            update_stats_display()

        try:
            log_msg("  開始掃描論壇...")

            scan_forum_incremental(
                self.api, self.cache_dir,
                force_refresh=False,
                on_progress=on_scan_progress,
                on_article=on_article,
                cancel_check=lambda: self._cancel_event.is_set(),
            )

            if self._cancel_event.is_set():
                log_msg("\n  掃描已中斷，使用目前已掃描的資料。")

            if not stats:
                log_msg("  沒有找到任何被提及的小說。")
                return

            recommend_data = prepare_recommend_data(stats, self.cache_dir)

            self.app.call_from_thread(
                self.post_message,
                self.RecommendReady(recommend_data, self.api, self.cache_dir),
            )
        except Exception as e:
            tb = traceback.format_exc()
            log_msg(f"\n  錯誤: {e}\n\n{tb}")
            log.exception("論壇推薦統計載入失敗")

    def action_go_back(self):
        self._cancel_event.set()
        self.app.pop_screen()


# ── 推薦統計列表頁 ──

class RecommendScreen(NovelListScreen):
    def __init__(self, api: NoveliaAPI, recommend_data: list[dict], cache_dir: str):
        super().__init__(api, title="論壇推薦統計")
        self._recommend_data = recommend_data
        self._cache_dir = cache_dir
        self._cancel_enrich = threading.Event()

    KEYWORD_WIDTH = 50
    TITLE_WIDTH = 140

    def _setup_columns(self, table: DataTable):
        table.add_columns(" ", "標題", "總提到", "文章提到", "留言提到", "小說留言數", "標籤")

    def _load_data(self):
        self._items_original = list(self._recommend_data)
        self.items = self._recommend_data
        self.total_pages = 1
        self._refresh_table()
        # 背景逐筆載入尚未取得的小說資訊
        has_unenriched = any(not item.get("enriched") for item in self.items)
        if has_unenriched:
            self.run_worker(self._enrich_missing, thread=True)

    def _get_display_title(self, item: dict) -> str:
        title = item.get("title", "")
        title = " ".join(title.split())
        return normalize_for_table(_get_s2twp().convert(title))

    def _get_row_data(self, item: dict) -> tuple:
        title = self._get_display_title(item)
        title = truncate_to_width(title, self.TITLE_WIDTH)
        keywords = ", ".join(item.get("keywords") or [])
        keywords = truncate_to_width(normalize_for_table(keywords), self.KEYWORD_WIDTH)
        return (
            title,
            str(item.get("total_count", 0)),
            str(item.get("article_count", 0)),
            str(item.get("comment_count", 0)),
            str(item.get("novel_comment_count", 0)),
            keywords,
        )

    def _enrich_missing(self):
        """背景逐筆取得小說資訊並更新表格列。"""
        from recommend import fetch_novel_info, load_novel_info_cache, save_novel_info_cache

        novel_cache = load_novel_info_cache(self._cache_dir)
        unenriched = [item for item in self.items if not item.get("enriched")]
        total = len(unenriched)

        for i, item in enumerate(unenriched):
            if self._cancel_enrich.is_set():
                break

            provider = item["provider"]
            novel_id = item["novel_id"]
            info = fetch_novel_info(self.api, provider, novel_id)

            if info:
                key = f"{provider}/{novel_id}"
                novel_cache[key] = info
                save_novel_info_cache(self._cache_dir, novel_cache)

                item["title"] = info["title"]
                item["keywords"] = info["keywords"]
                item["novel_comment_count"] = info["novel_comment_count"]
                item["attentions"] = info["attentions"]
            item["enriched"] = True

            self.app.call_from_thread(self._on_enrich_update, i + 1, total)

    def _on_enrich_update(self, current: int, total: int):
        self._refresh_table()
        sort_name = self.SORT_MODES[self._sort_index][0]
        status = self.query_one("#status-bar", Label)
        if current < total:
            status.update(
                f"  共 {len(self.items)} 筆"
                f"    排序: {sort_name}"
                f"    小說資訊載入中 {current}/{total}"
            )
        else:
            status.update(
                f"  共 {len(self.items)} 筆"
                f"    排序: {sort_name}"
                f"    小說資訊載入完成"
            )

    def _get_selected_item(self) -> dict | None:
        table = self.query_one("#novel-table", DataTable)
        if table.row_count == 0:
            return None
        cursor_key = list(table.rows.keys())[table.cursor_row]
        idx = int(cursor_key.value)
        if 0 <= idx < len(self.items):
            return self.items[idx]
        return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        item = self._get_selected_item()
        if item:
            provider = item.get("provider", "")
            novel_id = item.get("novel_id", "")
            if provider and novel_id:
                self.app.push_screen(NovelDetailScreen(self.api, provider, novel_id))

    def on_unmount(self):
        self._cancel_enrich.set()


# ── 主應用（含主選單） ──

class NoveliaApp(App):
    CSS = """
    #status-bar {
        height: 1;
        background: $primary-background;
        color: $text;
        padding: 0 1;
    }
    #filter-input {
        height: 3;
        margin: 0 1;
    }
    #detail-content {
        padding: 1 2;
    }
    #loading-status {
        padding: 1 2;
    }
    DataTable {
        height: 1fr;
    }
    #menu-title {
        text-style: bold;
        padding: 1 2;
    }
    #main-menu {
        height: auto;
        margin: 0 2;
    }
    #provider {
        height: 8;
        margin: 0 2;
    }
    .form-title {
        text-style: bold;
        padding: 1 0;
    }
    .form-row {
        height: auto;
    }
    .form-field {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    """

    TITLE = "Novelia Searcher"

    def __init__(self, api: NoveliaAPI, cache_dir: str = "./cache"):
        log.info("NoveliaApp.__init__ 開始")
        super().__init__()
        self.api = api
        self.cache_dir = cache_dir
        _init_s2twp()
        log.info("NoveliaApp.__init__ 完成")

    def compose(self) -> ComposeResult:
        log.info("NoveliaApp.compose 開始")
        yield Header()
        yield Static("\n  Novelia Searcher\n", id="menu-title")
        yield OptionList(
            Option("搜尋小說", id="search"),
            Option("論壇推薦統計", id="recommend"),
            Option("離開", id="quit"),
            id="main-menu",
        )
        yield Footer()
        log.info("NoveliaApp.compose 完成")

    def on_mount(self):
        log.info("NoveliaApp.on_mount 觸發")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        log.info(f"選單選擇: {event.option.id}")
        option_id = event.option.id
        if option_id == "search":
            self.push_screen(SearchFormScreen(self.api))
        elif option_id == "recommend":
            self.push_screen(
                RecommendLoadingScreen(self.api, self.cache_dir)
            )
        elif option_id == "quit":
            self.exit()

    def on_recommend_loading_screen_recommend_ready(
        self, event: RecommendLoadingScreen.RecommendReady
    ):
        self.pop_screen()
        self.push_screen(RecommendScreen(event.api, event.data, event.cache_dir))
