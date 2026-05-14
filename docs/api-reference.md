# Novelia API Reference

Base URL: `https://n.novelia.cc/api`

---

## 1. 論壇文章列表

**GET** `/article?page={page}&pageSize={pageSize}&category={category}`

| 參數 | 型別 | 說明 |
|------|------|------|
| `page` | int | 頁碼，從 0 開始 |
| `pageSize` | int | 每頁筆數 |
| `category` | string | 分類，已知值：`General`(小說交流)，`Guide`(使用指南)，`Support`(反饋與建議)，但只需統計小說交流|

**回應：**
```json
{
  "items": [
    {
      "id": "string",           // 文章 ID
      "title": "string",        // 文章標題
      "category": "string",     // 分類
      "locked": false,          // 是否鎖定
      "pinned": false,          // 是否置頂
      "hidden": false,          // 是否隱藏
      "numViews": 20122,        // 瀏覽次數
      "numComments": 3956,      // 留言數量
      "user": {
        "username": "string"    // 發文者
      },
      "createAt": 1739734288,   // 建立時間 (Unix timestamp)
      "updateAt": 1742489964    // 更新時間 (Unix timestamp)
    }
  ],
  "pageNumber": 30              // 總頁數
}
```

---

## 2. 論壇文章內容

**GET** `/article/{articleId}`

| 參數 | 型別 | 說明 |
|------|------|------|
| `articleId` | string | 文章 ID |

**回應：**
```json
{
  "id": "string",
  "title": "string",
  "content": "string",         // Markdown 格式
  "category": "string",
  "locked": true,
  "pinned": true,
  "hidden": false,
  "numViews": 10475,
  "numComments": 962,
  "user": {
    "username": "string"
  },
  "createAt": 1693704832,
  "updateAt": 1745258734
}
```

---

## 3. 留言列表（通用）

適用於論壇文章留言與小說留言，差異在 `site` 參數格式。

**GET** `/comment?page={page}&pageSize={pageSize}&site={site}`

| 參數 | 型別 | 說明 |
|------|------|------|
| `page` | int | 頁碼，從 0 開始 |
| `pageSize` | int | 每頁筆數 |
| `site` | string | 論壇文章：`article-{articleId}`；小說：`web-{provider}-{novelId}` |

**回應：**
```json
{
  "items": [
    {
      "id": "string",
      "user": {
        "username": "string"
      },
      "content": "string",
      "hidden": false,
      "createAt": 1710064609,   // Unix timestamp
      "numReplies": 1,
      "replies": [
        {
          "id": "string",
          "user": { "username": "string" },
          "content": "string",
          "hidden": false,
          "createAt": 1710064609,
          "numReplies": 0,
          "replies": []
        }
      ]
    }
  ],
  "pageNumber": 27             // 總頁數
}
```

---

## 4. 小說列表

> **注意：此 API 回傳 401，可能需要認證（Cookie / Token），待確認。**

**GET** `/novel?page={page}&pageSize={pageSize}&query={query}&provider={provider}&type={type}&level={level}&translate={translate}&sort={sort}`

| 參數 | 型別 | 說明 |
|------|------|------|
| `page` | int | 頁碼，從 0 開始 |
| `pageSize` | int | 每頁筆數 |
| `query` | string | 搜尋關鍵字 |
| `provider` | string | 來源平台，逗號分隔。已知值：`kakuyomu`, `syosetu`, `novelup`, `hameln`, `pixiv`, `alphapolis` |
| `type` | int | 0: 全部、1: 連載中、2: 已完結、3: 短篇 |
| `level` | int | 0: 全部、1: 一般向、2: R18 |
| `translate` | int | 0: 全部、1: GPT、2: Sakura |
| `sort` | int | 0: 更新、1: 點擊、2: 相關 |

**認證：** 需要 Bearer Token，放在 `Authorization` header。

**回應：**
```json
{
  "items": [
    {
      "providerId": "string",     // 來源平台，如 "kakuyomu", "syosetu"
      "novelId": "string",        // 小說 ID
      "titleJp": "string",        // 日文標題
      "titleZh": "string",        // 中文標題（可能為 null）
      "type": "string",           // 小說類型
      "attentions": ["string"],   // 注意事項標籤
      "keywords": ["string"],     // 關鍵字標籤
      "total": 10,                // 總章數
      "jp": 0,                    // 原文總計數量
      "baidu": 0,                 // 百度翻譯數量
      "youdao": 0,                // 有道翻譯數量
      "gpt": 0,                   // GPT 翻譯數量
      "sakura": 0,                // sakura 翻譯數量
      "updateAt": 1778739231      // 更新時間 (Unix timestamp)
    }
  ],
  "pageNumber": 5000              // 總頁數
}

---

## 5. 小說詳情

**GET** `/novel/{provider}/{novelId}`

| 參數 | 型別 | 說明 |
|------|------|------|
| `provider` | string | 來源平台，例如 `syosetu` |
| `novelId` | string | 小說 ID |

**回應：**
```json
{
  "titleJp": "string",         // 日文標題
  "titleZh": "string",         // 中文標題
  "authors": [
    {
      "name": "string",
      "link": "string"          // 作者頁面連結
    }
  ],
  "type": "string",             // 小說類型
  "attentions": ["string"],     // 注意事項標籤
  "keywords": ["string"],       // 關鍵字標籤
  "points": 0,                  // 評分/點數
  "totalCharacters": 0,         // 總字數
  "introductionJp": "string",   // 日文簡介
  "introductionZh": "string",   // 中文簡介
  "glossary": {                 // 術語對照表（日→中）
    "key": "value"
  },
  "toc": [                      // 目錄
    {
      "titleJp": "string",
      "titleZh": "string",
      "chapterId": "string",
      "createAt": 0
    }
  ],
  "visited": 0,                 // 瀏覽次數
  "syncAt": 0,                  // 最後同步時間
  "jp": 0,                      // 原文總計數量
  "baidu": 0,                   // 百度翻譯數量
  "youdao": 0,                  // 有道翻譯數量
  "gpt": 0,                     // GPT 翻譯數量
  "sakura": 0                   // sakura 翻譯數量
}
```

---

## 待確認事項

- [ ] 小說列表 API 的認證方式（Cookie? Bearer Token?） 
  - Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhMTQ3ODUyYiIsImF1ZCI6WyJuIl0sImV4cCI6MTc3OTM0Mzg1NSwiaWF0IjoxNzc4NzM5MDU1LCJyb2xlIjoibWVtYmVyIiwiY3JhdCI6MTc3NTAwOTA3M30.2eMkadGzruud22ehOmD-8uE9sI8zj9rr_e0VMHAMFWs

- [ ] `type`, `level`, `translate`, `sort` 各數值的對應意義
  - 已填寫
- [ ] `jp`, `baidu`, `youdao`, `gpt`, `sakura` 欄位意義
  - 已填寫
- [ ] `category` 的完整列表
  - 已填寫
- [ ] 是否有其他未列出的 API 端點
  - 不知道
