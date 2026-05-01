#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yahoo!ショッピング 順位スクレイピングスクリプト

機能:
- 手元PCで Playwright を使用して Yahoo!ショッピング検索結果を取得
- 各キーワードで30位までの全商品を取得
- PRマーク有無で広告枠/SEO枠を判別
- 自店舗(yukaiya)の商品を識別し、枠内順位を抽出
- 判定ルールに従って評価値を rank.json に書き込み
- 自動で git add/commit/push まで実行

判定ルール:
■広告順位(広告枠の中での順位)
  ・広告枠1〜4位 → "1～4位\n◯"
  ・広告枠5〜6位 → "5～6位\n△"
  ・広告枠7位以上 or 圏外 → "7位以下\n×"

■SEO順位(SEO枠の中での順位)
  ・SEO枠1〜6位 → "1～6位\n◯"
  ・SEO枠7〜14位 → "7～14位\n△"
  ・SEO枠15位以上 or 圏外 → "15位以下\n×"

■エラー時(取得失敗・タイムアウト等)
  ・rank.jsonには何も書き込まない(null=空白表示)
  ・圏外(×)とエラー(空白)を視覚的に区別できる
"""

import asyncio
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, Page, ElementHandle

# ==== 設定 ====
STORE_ID = "yukaiya"
SEARCH_URL = "https://shopping.yahoo.co.jp/search?p={keyword}"
TARGET_RANK = 30  # 取得する全体順位の上限
KEYWORD_INTERVAL_SEC = 8  # 各キーワード間の待機時間(マナー)
PAGE_LOAD_TIMEOUT_MS = 30_000  # ページロードタイムアウト
SCROLL_WAIT_MS = 1500  # スクロール後の待機
MAX_SCROLL_ATTEMPTS = 10  # スクロールリトライ上限

# Playwrightセレクタ(部分一致でハッシュ変動に対応)
ITEM_CONTAINER_SELECTOR = '[class*="SearchResult_SearchResultItem_"]'
PR_BADGE_SELECTOR = '[class*="imageIcon--pr"]'
ITEM_IMAGE_SELECTOR = 'img'

# ==== パス設定 ====
ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_CSV = ROOT / "data" / "products.csv"
RANK_JSON = ROOT / "rank.json"
LOG_DIR = ROOT / "logs"

JST = timezone(timedelta(hours=9))


def log(msg: str) -> None:
    """ログ出力(タイムスタンプ付き)"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)


def load_products(csv_path: Path) -> list[dict]:
    """商品マスタCSV(code, keyword)を読み込む"""
    products = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("code") or row.get("商品コード") or "").strip().lower()
            keyword = (row.get("keyword") or row.get("メインKW") or "").strip()
            if code and keyword:
                products.append({"code": code, "keyword": keyword})
    return products


def load_existing_rank_json(path: Path) -> dict:
    """既存のrank.jsonを読み込む。なければ空dict"""
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠ rank.json読み込み失敗: {e} → 新規作成")
    return {}


def days_in_month(year: int, month: int) -> int:
    """指定年月の日数を返す"""
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def set_today_value(
    data: dict,
    ym: str,
    code: str,
    field: str,
    day_index: int,
    days: int,
    value: str,
) -> None:
    """指定日のセルに値を書き込む。配列は月の日数分確保。"""
    if ym not in data:
        data[ym] = {}
    if code not in data[ym]:
        data[ym][code] = {}
    if field not in data[ym][code]:
        data[ym][code][field] = [None] * days
    if len(data[ym][code][field]) < days:
        data[ym][code][field].extend([None] * (days - len(data[ym][code][field])))
    data[ym][code][field][day_index] = value


# ==== 判定ロジック ====

def judge_ad_rank(rank: int | None) -> str:
    """広告枠順位の判定"""
    if rank is None:
        return "7位以下\n×"  # 圏外
    if 1 <= rank <= 4:
        return "1～4位\n◯"
    if 5 <= rank <= 6:
        return "5～6位\n△"
    return "7位以下\n×"


def judge_seo_rank(rank: int | None) -> str:
    """SEO枠順位の判定"""
    if rank is None:
        return "15位以下\n×"  # 圏外
    if 1 <= rank <= 6:
        return "1～6位\n◯"
    if 7 <= rank <= 14:
        return "7～14位\n△"
    return "15位以下\n×"


# ==== コード抽出 ====

CODE_PATTERN = re.compile(r'/i/j/([\w\-]+?)_([\w\-]+?)(?:[?&]|$)')


def extract_store_and_code(image_src: str) -> tuple[str, str] | None:
    """
    画像URLから (store_id, item_code) を抽出。
    例: "https://item-shopping.c.yimg.jp/i/j/yukaiya_foot-23-03-4set?resolution=2x"
        → ("yukaiya", "foot-23-03-4set")
    """
    if not image_src:
        return None
    m = CODE_PATTERN.search(image_src)
    if not m:
        return None
    return m.group(1).lower(), m.group(2).lower()


# ==== スクレイピング ====

async def ensure_items_loaded(page: Page, target_count: int) -> int:
    """30件以上の商品が読み込まれるまでスクロール。実際の取得件数を返す。"""
    last_count = 0
    for attempt in range(MAX_SCROLL_ATTEMPTS):
        items = await page.query_selector_all(ITEM_CONTAINER_SELECTOR)
        count = len(items)
        if count >= target_count:
            return count
        if count == last_count and attempt > 1:
            # 2回連続で件数が変わらないなら諦める
            return count
        last_count = count
        # ページ最下部までスクロール
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_WAIT_MS)
    items = await page.query_selector_all(ITEM_CONTAINER_SELECTOR)
    return len(items)


async def parse_item(item: ElementHandle) -> dict | None:
    """
    1商品の情報を抽出。
    戻り値: {"is_ad": bool, "store_id": str, "item_code": str} または None
    """
    try:
        # PRマークの有無
        pr_element = await item.query_selector(PR_BADGE_SELECTOR)
        is_ad = pr_element is not None

        # 画像URLから商品コードを抽出
        img = await item.query_selector(ITEM_IMAGE_SELECTOR)
        if img is None:
            return None
        src = await img.get_attribute("src")
        if not src:
            # data-src など別属性も試す
            src = await img.get_attribute("data-src") or ""
        result = extract_store_and_code(src)
        if result is None:
            return None
        store_id, item_code = result
        return {"is_ad": is_ad, "store_id": store_id, "item_code": item_code}
    except Exception:
        return None


async def scrape_keyword(page: Page, keyword: str) -> tuple[list[dict], list[dict]]:
    """
    1キーワードで検索し、30位までを取得。
    戻り値: (ad_items, seo_items)
      ad_items[i] = {"rank": 枠内順位, "store_id": ..., "item_code": ...}
      seo_items[i] = 同上
    """
    encoded_keyword = quote_plus(keyword)
    url = SEARCH_URL.format(keyword=encoded_keyword)

    await page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
    # 商品要素が出るまで待つ
    try:
        await page.wait_for_selector(ITEM_CONTAINER_SELECTOR, timeout=15_000)
    except Exception:
        log(f"  ⚠ 商品要素が見つからない (keyword='{keyword}')")
        return [], []

    # 30件揃うまでスクロール
    loaded_count = await ensure_items_loaded(page, TARGET_RANK)

    # 全商品を取得して、上位30位までを処理
    items = await page.query_selector_all(ITEM_CONTAINER_SELECTOR)
    items = items[:TARGET_RANK]

    ad_items = []
    seo_items = []
    ad_rank_counter = 0
    seo_rank_counter = 0

    for idx, item in enumerate(items, start=1):
        info = await parse_item(item)
        if info is None:
            continue
        if info["is_ad"]:
            ad_rank_counter += 1
            ad_items.append({
                "rank": ad_rank_counter,
                "overall": idx,
                "store_id": info["store_id"],
                "item_code": info["item_code"],
            })
        else:
            seo_rank_counter += 1
            seo_items.append({
                "rank": seo_rank_counter,
                "overall": idx,
                "store_id": info["store_id"],
                "item_code": info["item_code"],
            })

    return ad_items, seo_items


def find_self_rank(items: list[dict], target_code: str) -> int | None:
    """枠内アイテムリストから自店舗(yukaiya)の最上位順位を返す"""
    for item in items:
        if item["store_id"] == STORE_ID and item["item_code"] == target_code:
            return item["rank"]
    return None


# ==== git push ====

def git_commit_and_push(repo_root: Path) -> bool:
    """rank.jsonをcommit&pushする。成功時 True"""
    try:
        # 変更があるか確認
        result = subprocess.run(
            ["git", "diff", "--quiet", "rank.json"],
            cwd=repo_root,
            capture_output=True,
        )
        if result.returncode == 0:
            log("git: rank.jsonに変更なし、commit不要")
            return True

        # add
        subprocess.run(["git", "add", "rank.json"], cwd=repo_root, check=True)

        # commit
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        commit_msg = f"chore: update rank.json ({timestamp})"
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

        # push
        subprocess.run(["git", "push"], cwd=repo_root, check=True, capture_output=True)
        log(f"git: push成功 ({commit_msg})")
        return True
    except subprocess.CalledProcessError as e:
        log(f"✖ git操作エラー: {e}")
        if e.stderr:
            log(f"  stderr: {e.stderr.decode('utf-8', errors='replace')}")
        return False
    except Exception as e:
        log(f"✖ git操作で予期せぬエラー: {e}")
        return False


# ==== メイン ====

async def main_async() -> int:
    if not PRODUCTS_CSV.exists():
        log(f"✖ 商品マスタが見つかりません: {PRODUCTS_CSV}")
        return 1

    products = load_products(PRODUCTS_CSV)
    log(f"対象商品数: {len(products)}件")
    if not products:
        log("✖ 対象商品が0件です")
        return 1

    now_jst = datetime.now(JST)
    ym = now_jst.strftime("%Y-%m")
    year, month, day = now_jst.year, now_jst.month, now_jst.day
    day_index = day - 1
    total_days = days_in_month(year, month)

    log(f"=== スクレイピング開始: {ym} / 日={day} (index={day_index}) ===")

    rank_data = load_existing_rank_json(RANK_JSON)
    if ym not in rank_data:
        rank_data[ym] = {}

    success = 0
    ad_hit = 0
    seo_hit = 0
    errors = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        for i, product in enumerate(products, start=1):
            code = product["code"]
            keyword = product["keyword"]
            try:
                ad_items, seo_items = await scrape_keyword(page, keyword)
                self_ad_rank = find_self_rank(ad_items, code)
                self_seo_rank = find_self_rank(seo_items, code)

                ad_value = judge_ad_rank(self_ad_rank)
                seo_value = judge_seo_rank(self_seo_rank)

                set_today_value(rank_data, ym, code, "ad", day_index, total_days, ad_value)
                set_today_value(rank_data, ym, code, "seo", day_index, total_days, seo_value)

                ad_disp = f"広告{self_ad_rank}位" if self_ad_rank else "広告圏外"
                seo_disp = f"SEO{self_seo_rank}位" if self_seo_rank else "SEO圏外"
                log(f"[{i}/{len(products)}] {code} (KW='{keyword}'): {ad_disp}/{seo_disp}")

                success += 1
                if self_ad_rank:
                    ad_hit += 1
                if self_seo_rank:
                    seo_hit += 1
            except Exception as e:
                log(f"[{i}/{len(products)}] {code} (KW='{keyword}'): ✖エラー {e}")
                # エラー時はrank.jsonに値を書き込まない(nullのまま=空白表示)
                # → 圏外(×)とエラー(空白)を区別できる
                errors += 1

            # マナー上のキーワード間待機
            await asyncio.sleep(KEYWORD_INTERVAL_SEC)

        await browser.close()

    # meta情報
    rank_data["meta"] = {
        "last_updated": now_jst.isoformat(),
        "total_products": len(products),
        "success_count": success,
        "ad_hit_count": ad_hit,
        "seo_hit_count": seo_hit,
        "error_count": errors,
        "method": "scraping",
    }

    # 保存
    with RANK_JSON.open("w", encoding="utf-8") as f:
        json.dump(rank_data, f, ensure_ascii=False, indent=2)

    log(f"=== 完了 === 成功:{success} 広告ヒット:{ad_hit} SEOヒット:{seo_hit} エラー:{errors}")
    if errors > 0:
        log(f"⚠️ {errors}件のエラーが発生しました。Ycheck上で空白セルが表示される商品があります。")
        log(f"⚠️ 詳細は上記のログでエラー内容を確認してください。")

    # git push
    log("--- git push実行 ---")
    git_commit_and_push(ROOT)

    return 0 if errors < len(products) * 0.5 else 1  # 半分以上失敗ならエラー終了


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        log("中断されました")
        return 130
    except Exception as e:
        log(f"✖ 致命的エラー: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
