# 推薦統計同步邏輯

## 總覽

使用者從主選單進入「論壇推薦統計」時，系統會同時執行三件事：

```
┌─────────────────────────────────────────────────────────┐
│                    RecommendScreen                      │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  執行緒 1    │  │  執行緒 2    │  │  主執行緒    │   │
│  │  掃描論壇    │  │  載入小說資訊│  │  使用者操作  │   │
│  │              │  │              │  │  推薦列表    │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │           │
│         ▼                 ▼                 ▼           │
│  ┌─────────────────────────────────────────────────┐    │
│  │           共用狀態（加鎖保護）                   │    │
│  │  _stats, _article_stats, _novel_cache, ...      │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## 快取檔案

所有快取存放在 `config.json` 的 `cache_dir`（預設 `./cache`）下：

| 檔案 | 說明 | 結構 |
|------|------|------|
| `forum_cache.json` | 論壇文章原始資料 | `{article_id: {article, comments, updateAt}, _meta: {total_articles}}` |
| `stats_cache.json` | 每篇文章提取的小說連結（統計中間結果） | `{article_id: {updateAt, title, article_links, comment_links}}` |
| `novel_info_cache.json` | 小說詳細資訊（標題、標籤等） | `{provider/novel_id: {title, keywords, novel_comment_count, attentions}}` |

三層快取的關係：

```
forum_cache.json          stats_cache.json          novel_info_cache.json
（文章原始內容）    ──→   （提取出的連結）    ──→    （小說名稱/標籤）
  由 scan_forum_           由 compute_entry_          由 fetch_novel_info
  incremental 維護         links 計算                  從 API 取得
```

---

## 啟動流程（`_load_data`）

使用者選擇「論壇推薦統計」後，直接進入 `RecommendScreen`：

```
_load_data()
  │
  ├─ 1. 載入 novel_info_cache.json → self._novel_cache
  │     已有小說資訊的 key 加入 self._seen_novels
  │
  ├─ 2. 載入 stats_cache.json → self._article_stats
  │     │
  │     └─ 如果有資料：
  │          ├─ build_stats_from_article_stats() → self._stats（聚合統計）
  │          ├─ 將 _stats 中不在 _seen_novels 的小說放入 _enrich_queue
  │          └─ _rebuild_items() → 立即在表格中顯示列表
  │
  ├─ 3. 啟動執行緒 1：_scan_worker
  │
  └─ 4. 啟動執行緒 2：_enrich_worker
```

**關鍵效果**：如果有 `stats_cache.json`，使用者一進來就能看到列表，不用等掃描。

---

## 執行緒 1：掃描論壇（`_scan_worker`）

負責掃描論壇文章，更新 `forum_cache.json` 和 `stats_cache.json`。

### 內部呼叫 `scan_forum_incremental`

這個函式分兩階段：

#### Phase 1：取得文章列表

```
呼叫 API 取得文章列表（每頁 40 篇）
  │
  ├─ 提前結束優化：
  │    條件：快取文章數 == 上次記錄的總數 AND 該頁所有文章的 updateAt 都與快取一致
  │    效果：跳過剩餘頁面，把快取中的舊文章補入列表
  │
  └─ 完整取得文章列表後，清理快取中已被刪除的文章
```

#### Phase 2：逐篇處理

```
對每篇文章 (article_id, updateAt)：
  │
  ├─ forum_cache 命中（article_id 存在且 updateAt 一致）？
  │    ├─ 是 → 不呼叫 API，直接用快取資料呼叫 on_article(entry, is_cached=True)
  │    └─ 否 → 呼叫 API 取得文章內容和留言 → 存入 forum_cache → on_article(entry, is_cached=False)
  │
  └─ 每處理完一篇就呼叫 on_article 回呼
```

### `on_article` 回呼（在 `_scan_worker` 中定義）

```
on_article(entry, is_cached) 被呼叫時：
  │
  ├─ stats_cache 命中（article_id 存在且 updateAt 一致）？
  │    ├─ 是 → 跳過，只更新狀態列
  │    └─ 否 → 進入下面的處理
  │
  ├─ compute_entry_links(entry)
  │    從文章內容和留言中用正則提取 novelia 小說連結
  │    回傳 {title, article_links: [(provider, novel_id), ...],
  │           comment_links: [(provider, novel_id), ...]}
  │
  ├─ 更新 self._article_stats[article_id] = {updateAt, title, article_links, comment_links}
  │
  ├─ build_stats_from_article_stats(self._article_stats)
  │    從所有文章的連結重新聚合統計 → self._stats
  │    統計內容：每部小說被幾篇文章提到、被幾則留言提到
  │
  ├─ 將新發現的小說 key 放入 _enrich_queue（給執行緒 2 處理）
  │
  ├─ save_stats_cache() → 寫入 stats_cache.json
  │
  └─ call_from_thread(_rebuild_items) → 通知主執行緒更新表格
```

### 掃描結束後

```
scan_forum_incremental 回傳 forum_data（所有文章）
  │
  ├─ 比對 _article_stats 的 key 與 forum_data 的 article_id
  │    找出已被刪除的文章 → 從 _article_stats 中移除
  │    → 重新 build_stats_from_article_stats
  │    → save_stats_cache
  │    → _rebuild_items
  │
  └─ 設定 _scan_done → 通知執行緒 2 可以結束
```

---

## 執行緒 2：載入小說資訊（`_enrich_worker`）

從 `_enrich_queue` 取出小說 key，呼叫 API 取得名稱、標籤等資訊。

```
迴圈（直到取消或掃描完成且佇列為空）：
  │
  ├─ 從 _enrich_queue 取出 (provider, novel_id)，等待 0.5 秒 timeout
  │
  ├─ _novel_cache 已有此 key？
  │    ├─ 是 → 跳過，計數 +1
  │    └─ 否 → 呼叫 fetch_novel_info(api, provider, novel_id)
  │              ├─ API: GET /novel/{provider}/{novel_id} → 取得標題、標籤
  │              └─ API: GET /comment?site=web-{provider}-{novel_id} → 取得留言數
  │
  ├─ 寫入 _novel_cache[key] = {title, keywords, novel_comment_count, attentions}
  ├─ save_novel_info_cache() → 寫入 novel_info_cache.json
  │
  └─ call_from_thread(_rebuild_items) → 通知主執行緒更新表格
      （列表中的標題會從 "provider/novel_id" 變成實際小說名稱）
```

### 結束條件

```
_enrich_queue 為空 AND _scan_done 已設定 → 退出迴圈
設定 _enrich_done → 狀態列顯示「小說資訊載入完成」
```

---

## 主執行緒：UI 更新

### `_rebuild_items()`

由執行緒 1 和 2 透過 `call_from_thread` 觸發，在主執行緒中執行：

```
_rebuild_items()
  │
  ├─ 從 self._stats 取得聚合統計（加鎖讀取）
  │
  ├─ 對每個 (provider, novel_id)：
  │    ├─ _novel_cache 有此 key？→ 使用標題、標籤、留言數
  │    └─ 沒有？→ 標題顯示為 "provider/novel_id"
  │
  ├─ 依 total_count 排序
  │
  └─ 更新 DataTable 表格 + 狀態列
```

### 狀態列格式

```
共 42 筆    排序: 預設    掃描: 文章 15/120（快取）    小說資訊: 8/42
                          ↑ 掃描中                      ↑ 載入中

共 42 筆    排序: 預設    掃描完成（120 篇）    小說資訊載入完成
                          ↑ 掃描結束             ↑ 全部載入完成
```

---

## 取消機制

使用者按 Escape → `_cancel_event.set()`：

```
_cancel_event 被設定後：
  ├─ scan_forum_incremental 的 cancel_check 回傳 True → 停止掃描
  ├─ _enrich_worker 的迴圈條件檢查 → 停止載入
  └─ pop_screen() → 返回主選單
```

已處理的資料都已即時寫入快取檔案，下次進來時可從中繼續。

---

## 兩層快取命中判斷的差異

同一篇文章會經過兩層快取判斷，各自的意義不同：

| 層 | 位置 | 判斷 | 命中時的效果 |
|----|------|------|-------------|
| **forum_cache** | `scan_forum_incremental` Phase 2 | `article_id` 存在且 `updateAt` 一致 | 跳過 API 呼叫（不重新抓文章內容），直接用快取的 `{article, comments}` |
| **stats_cache** | `on_article` 回呼 | `article_id` 存在且 `updateAt` 一致 | 跳過連結提取（不重新 parse 文章內容），統計結果沿用快取 |

```
文章 A（未變動）：
  forum_cache 命中 → 不呼叫 API，用快取資料呼叫 on_article
    → stats_cache 命中 → 不重新提取連結，跳過

文章 B（已更新）：
  forum_cache 未命中 → 呼叫 API 取得新內容，存入 forum_cache，呼叫 on_article
    → stats_cache 未命中 → 重新提取連結，更新 stats_cache，重建聚合統計

文章 C（全新）：
  forum_cache 未命中 → 呼叫 API，存入 forum_cache，呼叫 on_article
    → stats_cache 沒有此 key → 提取連結，加入 stats_cache，重建聚合統計
```

---

## 資料流全景圖

```
                          Novelia API
                     ┌────────┴────────┐
                     │                 │
              GET /article        GET /novel/{p}/{id}
              GET /comment        GET /comment
                     │                 │
                     ▼                 ▼
            ┌────────────────┐  ┌──────────────────┐
            │ forum_cache    │  │ novel_info_cache  │
            │ .json          │  │ .json             │
            │                │  │                   │
            │ 文章原始內容   │  │ 小說標題/標籤     │
            └───────┬────────┘  └────────┬──────────┘
                    │                    │
          compute_entry_links            │
                    │                    │
                    ▼                    │
            ┌────────────────┐           │
            │ stats_cache    │           │
            │ .json          │           │
            │                │           │
            │ 每篇文章的     │           │
            │ 小說連結       │           │
            └───────┬────────┘           │
                    │                    │
      build_stats_from_article_stats     │
                    │                    │
                    ▼                    ▼
            ┌────────────────────────────────┐
            │        _rebuild_items          │
            │                                │
            │  聚合統計 + 小說資訊 → 表格列  │
            └────────────────────────────────┘
```
