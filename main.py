import json
import logging
import os
import sys

logging.basicConfig(
    filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug.log"),
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("main")

from api import NoveliaAPI
from ui import NoveliaApp

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"找不到設定檔: {CONFIG_PATH}")
        print('請建立 config.json，內容範例: {{"token": "your_token", "cache_dir": "./cache", "forum_scan_interval": 5}}')
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    log.info("=== 啟動 Novelia Searcher ===")
    config = load_config()
    log.info("設定檔載入成功")

    token = config.get("token", "")
    if not token:
        print("請先在 config.json 中設定 token。")
        sys.exit(1)

    cache_dir = config.get("cache_dir", "./cache")
    scan_interval = config.get("forum_scan_interval", 5)
    log.info(f"token 長度: {len(token)}, cache_dir: {cache_dir}, scan_interval: {scan_interval}")

    api = NoveliaAPI(token, scan_interval=scan_interval)
    log.info("API 客戶端建立完成")
    try:
        log.info("建立 NoveliaApp")
        app = NoveliaApp(api, cache_dir=cache_dir)
        log.info("呼叫 app.run()")
        app.run()
        log.info("app.run() 結束")
    except Exception as e:
        log.exception(f"app.run() 發生錯誤: {e}")
        raise
    finally:
        api.close()
        log.info("API 客戶端已關閉")


if __name__ == "__main__":
    main()
