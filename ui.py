import json
import logging
import os
import subprocess
import threading
import time as _time
import unicodedata
import webbrowser
from collections import defaultdict

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
import queue

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

TAG_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tag_map.json")
_tag_map: dict[str, str] = {}


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


def load_tag_map():
    """從 tag_map.json 載入標籤歸類表（variant → canonical）。"""
    global _tag_map
    try:
        with open(TAG_MAP_PATH, "r", encoding="utf-8") as f:
            _tag_map = json.load(f)
        log.info(f"載入 tag_map.json，共 {len(_tag_map)} 筆")
    except Exception as e:
        log.warning(f"載入 tag_map.json 失敗: {e}")
        _tag_map = {}


def normalize_tag(tag: str) -> str:
    """將標籤映射為正規化名稱，未定義則原樣回傳。"""
    return _tag_map.get(tag, tag)


load_char_map()
load_tag_map()


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
        keywords = ", ".join(dict.fromkeys(normalize_tag(k) for k in detail.get("keywords", [])))
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


# ── 標籤篩選頁 ──

class TagFilterScreen(Screen):
    """標籤篩選：多個 OR 群組以 AND 連結。

    例：群組1(A|B) AND 群組2(C|D) → 作品需同時符合兩組。
    """
    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("c", "copy_tag", "複製標籤"),
        Binding("a", "add_group", "加入群組"),
        Binding("d", "remove_group", "刪除上一群組"),
        Binding("q", "apply", "確認篩選"),
        Binding("backspace", "clear_all", "清除全部"),
    ]

    def __init__(self, tags_with_counts: list[tuple[str, int]],
                 groups: list[set[str]]):
        super().__init__()
        self._tags_with_counts = tags_with_counts
        self._groups: list[set[str]] = [set(g) for g in groups]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("", id="groups-display")
            yield Label("  選擇標籤，加入新的 OR 群組（Space 切換）:")
            selections = []
            for tag, count in self._tags_with_counts:
                display = f"{_get_s2twp().convert(tag)} ({count})"
                selections.append((display, tag, False))
            yield SelectionList(*selections, id="tag-list")
        yield Footer()

    def on_mount(self):
        self._update_groups_display()
        self.query_one("#tag-list", SelectionList).focus()

    def _update_groups_display(self):
        if not self._groups:
            text = "  （尚未建立篩選群組）"
        else:
            lines = []
            for i, group in enumerate(self._groups, 1):
                tags_str = " | ".join(
                    _get_s2twp().convert(t) for t in sorted(group)
                )
                lines.append(f"  群組 {i}: {tags_str}")
            lines.append("")
            lines.append("  群組之間為 AND，群組內為 OR")
            text = "\n".join(lines)
        self.query_one("#groups-display", Static).update(text)

    def _get_selected_tags(self) -> set[str]:
        return set(self.query_one("#tag-list", SelectionList).selected)

    def _clear_selection(self):
        tag_list = self.query_one("#tag-list", SelectionList)
        for tag, _count in self._tags_with_counts:
            tag_list.deselect(tag)

    def action_add_group(self):
        selected = self._get_selected_tags()
        if selected:
            self._groups.append(selected)
            self._clear_selection()
            self._update_groups_display()
        else:
            self.notify("請先選擇至少一個標籤", timeout=2)

    def action_remove_group(self):
        if self._groups:
            self._groups.pop()
            self._update_groups_display()

    def action_apply(self):
        selected = self._get_selected_tags()
        if selected:
            self._groups.append(selected)
        self.dismiss(self._groups)

    def action_clear_all(self):
        self.dismiss([])

    def action_copy_tag(self):
        tag_list = self.query_one("#tag-list", SelectionList)
        idx = tag_list.highlighted
        if idx is not None and 0 <= idx < len(self._tags_with_counts):
            tag = self._tags_with_counts[idx][0]
            subprocess.run(
                ["clip"], input=tag.encode("utf-16le"),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.notify(f"已複製: {tag}", timeout=2)

    def action_cancel(self):
        self.dismiss(None)


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
        Binding("t", "tag_filter", "標籤篩選"),
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
        self._tag_filter: list[set[str]] = []

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
        table.focus()
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
            if self._tag_filter:
                item_tags = {normalize_tag(t) for t in item.get("keywords") or []}
                if not all(group & item_tags for group in self._tag_filter):
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

    def _collect_tags(self) -> list[tuple[str, int]]:
        """從所有項目收集正規化後的標籤及出現次數，按次數降序排列。"""
        tag_counts: dict[str, int] = defaultdict(int)
        for item in self._items_original:
            for tag in item.get("keywords") or []:
                tag_counts[normalize_tag(tag)] += 1
        return sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))

    def action_tag_filter(self):
        tags = self._collect_tags()
        if not tags:
            self.notify("目前列表沒有標籤資料", timeout=2)
            return
        self.app.push_screen(
            TagFilterScreen(tags, list(self._tag_filter)),
            callback=self._on_tag_filter_result,
        )

    def _on_tag_filter_result(self, result):
        if result is None:
            return
        self._tag_filter = result
        self._refresh_table()

    def _update_status(self):
        sort_name = self.SORT_MODES[self._sort_index][0]
        status = self.query_one("#status-bar", Label)
        msg = (
            f"  第 {self.current_page + 1} 頁 / 共 {self.total_pages} 頁"
            f"    共 {len(self.items)} 筆"
            f"    排序: {sort_name}"
        )
        if self._tag_filter:
            msg += f"    標籤篩選: {len(self._tag_filter)} 組"
        status.update(msg)

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
        load_tag_map()
        self._refresh_table()
        status = self.query_one("#status-bar", Label)
        status.update(
            f"  字元替換表已重載（{len(_char_map)} 筆）"
            f"  標籤歸類表已重載（{len(_tag_map)} 筆）"
        )

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
        keywords = ", ".join(dict.fromkeys(normalize_tag(k) for k in item.get("keywords", [])))
        keywords = truncate_to_width(normalize_for_table(keywords), self.KEYWORDS_WIDTH)
        return (title, str(total), str(comments), date_str, keywords)


# ── 推薦統計列表頁 ──

class RecommendScreen(NovelListScreen):
    """三執行緒並行：掃描論壇 / 載入小說資訊 / 使用者操作列表。"""

    def __init__(self, api: NoveliaAPI, cache_dir: str, full_scan: bool = False):
        super().__init__(api, title="論壇推薦統計")
        self._cache_dir = cache_dir
        self._full_scan = full_scan
        self._cancel_event = threading.Event()
        # 掃描統計（由 scan 執行緒寫入，主執行緒讀取）
        self._stats: dict[tuple[str, str], dict] = {}
        self._stats_lock = threading.Lock()
        # 小說資訊並行載入
        self._novel_cache: dict[str, dict] = {}
        self._novel_cache_lock = threading.Lock()
        self._seen_novels: set[tuple[str, str]] = set()
        self._enrich_queue: queue.Queue[tuple[str, str]] = None  # type: ignore
        self._scan_done = threading.Event()
        self._enrich_done = threading.Event()
        self._article_stats: dict[str, dict] = {}
        self._scan_article_count = 0
        self._enrich_count = [0, 0]  # [completed, total_queued]
        self._scan_status_msg = ""

    KEYWORD_WIDTH = 50
    TITLE_WIDTH = 140
    PAGE_SIZE = 100

    def _setup_columns(self, table: DataTable):
        table.add_columns(" ", "標題", "總提到", "文章提到", "留言提到", "小說留言數", "標籤")

    def _load_data(self):
        from recommend import (load_novel_info_cache, load_stats_cache,
                               build_stats_from_article_stats)

        self._enrich_queue = queue.Queue()
        self._novel_cache = load_novel_info_cache(self._cache_dir)
        self._seen_novels = set(
            tuple(k.split("/", 1)) for k in self._novel_cache.keys() if "/" in k
        )

        # 載入 per-article 統計快取 → 立即顯示列表
        self._article_stats = load_stats_cache(self._cache_dir)
        if self._article_stats:
            with self._stats_lock:
                self._stats = build_stats_from_article_stats(self._article_stats)
                # 將已知小說加入 enrich 佇列
                for key in self._stats.keys():
                    if key not in self._seen_novels:
                        self._seen_novels.add(key)
                        self._enrich_count[1] += 1
                        self._enrich_queue.put(key)
            self._rebuild_items()

        # 啟動兩個背景執行緒
        self.run_worker(self._scan_worker, thread=True)
        self.run_worker(self._enrich_worker, thread=True)

    # ── 執行緒 1：掃描論壇 ──

    def _scan_worker(self):
        from recommend import (scan_forum_incremental, compute_entry_links,
                               save_stats_cache, build_stats_from_article_stats)

        changed = False

        def on_progress(current, total, msg):
            self._scan_status_msg = msg
            self.app.call_from_thread(self._update_status_bar)

        def on_article(entry, is_cached):
            nonlocal changed
            aid = entry["article"]["id"]
            update_at = entry.get("updateAt", 0)
            self._scan_article_count += 1

            # 統計快取命中 → 跳過
            cached = self._article_stats.get(aid)
            if cached and cached.get("updateAt") == update_at:
                self.app.call_from_thread(self._update_status_bar)
                return

            # 新文章或已更新 → 計算連結並更新快取
            changed = True
            links = compute_entry_links(entry)
            self._article_stats[aid] = {
                "updateAt": update_at,
                "title": links["title"],
                "article_links": links["article_links"],
                "comment_links": links["comment_links"],
            }

            with self._stats_lock:
                self._stats = build_stats_from_article_stats(self._article_stats)
                for key in self._stats.keys():
                    if key not in self._seen_novels:
                        self._seen_novels.add(key)
                        self._enrich_count[1] += 1
                        self._enrich_queue.put(key)
            save_stats_cache(self._cache_dir, self._article_stats)
            self.app.call_from_thread(self._rebuild_items)

        try:
            forum_data = scan_forum_incremental(
                self.api, self._cache_dir,
                force_refresh=False,
                full_scan=self._full_scan,
                on_progress=on_progress,
                on_article=on_article,
                cancel_check=lambda: self._cancel_event.is_set(),
            )

            # 清理已刪除的文章
            current_ids = {e["article"]["id"] for e in forum_data}
            deleted = [aid for aid in list(self._article_stats.keys())
                       if aid not in current_ids]
            if deleted:
                for aid in deleted:
                    del self._article_stats[aid]
                with self._stats_lock:
                    self._stats = build_stats_from_article_stats(self._article_stats)
                save_stats_cache(self._cache_dir, self._article_stats)
                self.app.call_from_thread(self._rebuild_items)

        except Exception as e:
            log.exception("論壇掃描失敗")
            self.app.call_from_thread(
                self.notify, f"掃描錯誤: {e}", severity="error"
            )
        finally:
            self._scan_done.set()
            self.app.call_from_thread(self._update_status_bar)

    # ── 執行緒 2：載入小說資訊 ──

    def _enrich_worker(self):
        from recommend import fetch_novel_info, save_novel_info_cache

        while not self._cancel_event.is_set():
            try:
                provider, novel_id = self._enrich_queue.get(timeout=0.5)
            except queue.Empty:
                if self._scan_done.is_set() and self._enrich_queue.empty():
                    break
                continue

            key = f"{provider}/{novel_id}"
            with self._novel_cache_lock:
                if key in self._novel_cache:
                    self._enrich_count[0] += 1
                    self._enrich_queue.task_done()
                    self.app.call_from_thread(self._rebuild_items)
                    continue

            info = fetch_novel_info(self.api, provider, novel_id)
            if info:
                with self._novel_cache_lock:
                    self._novel_cache[key] = info
                    save_novel_info_cache(self._cache_dir, self._novel_cache)
            self._enrich_count[0] += 1
            self._enrich_queue.task_done()
            self.app.call_from_thread(self._rebuild_items)

        self._enrich_done.set()
        self.app.call_from_thread(self._update_status_bar)

    # ── 主執行緒：更新 UI ──

    def _rebuild_items(self):
        """從 stats + novel_cache 重建列表資料並刷新表格。"""
        with self._stats_lock:
            stats_snapshot = dict(self._stats)

        results = []
        for (provider, novel_id), val in stats_snapshot.items():
            key = f"{provider}/{novel_id}"
            with self._novel_cache_lock:
                cached = self._novel_cache.get(key)
            if cached:
                title = cached.get("title", key)
                keywords = cached.get("keywords", [])
                novel_comment_count = cached.get("novel_comment_count", 0)
                attentions = cached.get("attentions", [])
            else:
                title = key
                keywords = []
                novel_comment_count = 0
                attentions = []

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
            })

        results.sort(key=lambda x: x["total_count"], reverse=True)
        self._items_original = results
        self._apply_sort()
        self._refresh_table()
        self._update_status_bar()

    def _get_filtered_items(self):
        """回傳過濾後的 (原始索引, item) 列表。"""
        filter_lower = self.filter_text.lower()
        result = []
        for i, item in enumerate(self.items):
            if filter_lower and filter_lower not in self._get_display_title(item).lower():
                continue
            if self._tag_filter:
                item_tags = {normalize_tag(t) for t in item.get("keywords") or []}
                if not all(group & item_tags for group in self._tag_filter):
                    continue
            result.append((i, item))
        return result

    def _update_total_pages(self):
        filtered = self._get_filtered_items()
        self.total_pages = max(1, -(-len(filtered) // self.PAGE_SIZE))
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

    def _refresh_table(self):
        filtered = self._get_filtered_items()
        self.total_pages = max(1, -(-len(filtered) // self.PAGE_SIZE))
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        start = self.current_page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        page_items = filtered[start:end]

        table = self.query_one("#novel-table", DataTable)
        prev_cursor = table.cursor_row if table.row_count > 0 else 0
        table.clear()
        for i, item in page_items:
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
        self._update_status_bar()

    def action_prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._refresh_table()

    def action_next_page(self):
        if self.current_page + 1 < self.total_pages:
            self.current_page += 1
            self._refresh_table()

    def _update_status_bar(self):
        sort_name = self.SORT_MODES[self._sort_index][0]
        parts = [
            f"  第 {self.current_page + 1}/{self.total_pages} 頁",
            f"共 {len(self.items)} 筆",
            f"排序: {sort_name}",
        ]

        if not self._scan_done.is_set():
            parts.append(f"掃描: {self._scan_status_msg}")
        else:
            parts.append(f"掃描完成（{self._scan_article_count} 篇）")

        if not self._enrich_done.is_set() and self._enrich_count[1] > 0:
            parts.append(f"小說資訊: {self._enrich_count[0]}/{self._enrich_count[1]}")
        elif self._enrich_done.is_set():
            parts.append("小說資訊載入完成")

        if self._tag_filter:
            parts.append(f"標籤篩選: {len(self._tag_filter)} 組")

        status = self.query_one("#status-bar", Label)
        status.update("    ".join(parts))

    def _get_display_title(self, item: dict) -> str:
        title = item.get("title", "")
        title = " ".join(title.split())
        return normalize_for_table(_get_s2twp().convert(title))

    def _get_row_data(self, item: dict) -> tuple:
        title = self._get_display_title(item)
        title = truncate_to_width(title, self.TITLE_WIDTH)
        keywords = ", ".join(dict.fromkeys(normalize_tag(k) for k in item.get("keywords") or []))
        keywords = truncate_to_width(normalize_for_table(keywords), self.KEYWORD_WIDTH)
        return (
            title,
            str(item.get("total_count", 0)),
            str(item.get("article_count", 0)),
            str(item.get("comment_count", 0)),
            str(item.get("novel_comment_count", 0)),
            keywords,
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
            self._cancel_event.set()
            self.app.pop_screen()

    def on_unmount(self):
        self._cancel_event.set()


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
    #tag-list {
        height: auto;
        max-height: 80vh;
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
            Option("論壇推薦統計（完整掃描）", id="recommend-full"),
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
                RecommendScreen(self.api, self.cache_dir)
            )
        elif option_id == "recommend-full":
            self.push_screen(
                RecommendScreen(self.api, self.cache_dir, full_scan=True)
            )
        elif option_id == "quit":
            self.exit()

