#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yahoo!ショッピング 順位スクレイピングスクリプト(本番版)

【v9で修正された重要点】
1. セレクタ修正:商品コンテナ本体のみを正しく抽出
   - 旧版:[class*="SearchResult_SearchResultItem_"] が部分一致のため
     子要素(__price, __image, __imageIcon等)も「商品」とカウント。
     最初の30要素のうち画像URLが取れるのは数件のみで、SEO検出が大幅に欠落していた。
   - 新版:JS evaluateで __ がちょうど1回のクラス名(本体)のみフィルタ。
     v9実機テストで3キーワード全て30/30件のコード取得成功。
2. git push バグ修正:
   - 旧版:git diff --quiet rank.json → 新規ファイルや stage 後の変更を検知できず
   - 新版:git add → git diff --cached --quiet で stage 後の差分を判定

機能:
- 手元PCで Playwright を使用して Yahoo!ショッピング検索結果を取得
- 各キーワードで30位までの全商品を取得
- PRマーク有無で広告枠/SEO枠を判別
- 自店舗(yukaiya)の商品を識別し、枠内順位を抽出
- 判定ルールに従って評価値を rank.json に書き込み
- 自動で git add/commit/push まで実行

判定ルール:
■広告順位(広告枠の中での順位)
  ・広告枠1〜4位 → "1~4位\n◯"
  ・広告枠5〜6位 → "5~6位\n△"
  ・広告枠7位以上 or 圏外 → "7位\n以下✕"

■SEO順位(SEO枠の中での順位)
  ・SEO枠1〜6位 → "1~6位\n◯"
  ・SEO枠7〜14位 → "7~14位\n△"
  ・SEO枠15位以上 or 圏外 → "15位\n以下✕"

■エラー時(取得失敗・タイムアウト等)
  ・rank.jsonには何も書き込まない(null=空白表示)
  ・圏外(✕)とエラー(空白)を視覚的に区別できる
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

from playwright.async_api import async_playwright, Page

# ==== 設定 ====
STORE_ID = "yukaiya"
SEARCH_URL = "https://shopping.yahoo.co.jp/search?p={keyword}"
TARGET_RANK = 30  # 取得する全体順位の上限
KEYWORD_INTERVAL_SEC = 8  # 各キーワード間の待機時間(マナー)
PAGE_LOAD_TIMEOUT_MS = 30_000  # ページロードタイムアウト
SCROLL_WAIT_MS = 400  # スクロール後の待機
MAX_SCROLL_STEPS = 15  # スクロール回数(画像遅延ロード対策)

# Playwrightセレクタ(部分一致でハッシュ変動に対応)
ITEM_CONTAINER_PRESENCE_SELECTOR = '[class*="SearchResult_SearchResultItem_"]'

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
    """広告枠順位の判定

    【v9.1】Ycheck側CSSの色付け判定が U+2715「✕」を期待しているため、× → ✕ に変更。
           また圏外/7位以下は「7位\\n以下✕」の改行位置に変更。
    【v9.2】全角チルダ「～」を半角「~」に変更(セル横幅を超えて3行になるのを防ぐため)。
    """
    if rank is None:
        return "7位\n以下✕"  # 圏外
    if 1 <= rank <= 4:
        return "1~4位\n◯"
    if 5 <= rank <= 6:
        return "5~6位\n△"
    return "7位\n以下✕"


def judge_seo_rank(rank: int | None) -> str:
    """SEO枠順位の判定

    【v9.1】Ycheck側CSSの色付け判定が U+2715「✕」を期待しているため、× → ✕ に変更。
           また圏外/15位以下は「15位\\n以下✕」の改行位置に変更。
    【v9.2】全角チルダ「～」を半角「~」に変更(セル横幅を超えて3行になるのを防ぐため)。
    """
    if rank is None:
        return "15位\n以下✕"  # 圏外
    if 1 <= rank <= 6:
        return "1~6位\n◯"
    if 7 <= rank <= 14:
        return "7~14位\n△"
    return "15位\n以下✕"


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

# JS式:本体コンテナのみを抽出して必要情報を返す
# 本体クラス名:SearchResult_SearchResultItem__<ハッシュ> ( __ が1回 )
# 子要素クラス名:SearchResult_SearchResultItem__<名前>__<ハッシュ> ( __ が2回以上 )
EXTRACT_ITEMS_JS = """
() => {
    const all = document.querySelectorAll('[class*="SearchResult_SearchResultItem"]');
    const mainItems = [];
    for (const el of all) {
        const classes = (el.getAttribute('class') || '').split(/\\s+/);
        let isMain = false;
        for (const c of classes) {
            if (c.startsWith('SearchResult_SearchResultItem__')) {
                const underscoreCount = (c.match(/__/g) || []).length;
                if (underscoreCount === 1) { isMain = true; break; }
            }
        }
        if (isMain) mainItems.push(el);
    }
    return mainItems.slice(0, 30).map((item) => {
        const pr = item.querySelector('[class*="imageIcon--pr"]');
        const img = item.querySelector('img');
        const src = img ? (img.getAttribute('src') || '') : '';
        const dataSrc = img ? (img.getAttribute('data-src') || '') : '';
        const srcset = img ? (img.getAttribute('srcset') || '') : '';
        return { is_ad: !!pr, src, dataSrc, srcset };
    });
}
"""


async def scroll_to_load_all(page: Page) -> None:
    """画像遅延ロード対策で段階的にスクロールしてから最上部に戻す"""
    for _ in range(MAX_SCROLL_STEPS):
        await page.evaluate("window.scrollBy(0, 500)")
        await page.wait_for_timeout(SCROLL_WAIT_MS)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(1000)


async def scrape_keyword(page: Page, keyword: str) -> tuple[list[dict], list[dict]]:
    """
    1キーワードで検索し、30位までを取得。
    戻り値: (ad_items, seo_items)
      ad_items[i] = {"rank": 枠内順位, "overall": 全体順位, "store_id": ..., "item_code": ...}
      seo_items[i] = 同上
    """
    encoded_keyword = quote_plus(keyword)
    url = SEARCH_URL.format(keyword=encoded_keyword)

    await page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")

    # 商品要素が出るまで待つ
    try:
        await page.wait_for_selector(ITEM_CONTAINER_PRESENCE_SELECTOR, timeout=15_000)
    except Exception:
        log(f"  ⚠ 商品要素が見つからない (keyword='{keyword}')")
        return [], []

    # 画像遅延ロード対策の段階的スクロール
    await scroll_to_load_all(page)

    # 本体コンテナだけをJS側で抽出
    items_data = await page.evaluate(EXTRACT_ITEMS_JS)

    ad_items: list[dict] = []
    seo_items: list[dict] = []
    ad_rank_counter = 0
    seo_rank_counter = 0

    for idx, d in enumerate(items_data, start=1):
        # 画像URLからコード抽出(src→data-src→srcsetの順)
        result = None
        for src_value in (d.get("src", ""), d.get("dataSrc", ""), d.get("srcset", "")):
            if src_value:
                result = extract_store_and_code(src_value)
                if result:
                    break
        if result is None:
            # コード取得失敗(アイテムリーチ広告など)→ 順位カウントは進めるがスキップ
            if d.get("is_ad"):
                ad_rank_counter += 1
            else:
                seo_rank_counter += 1
            continue

        store_id, item_code = result
        if d.get("is_ad"):
            ad_rank_counter += 1
            ad_items.append({
                "rank": ad_rank_counter,
                "overall": idx,
                "store_id": store_id,
                "item_code": item_code,
            })
        else:
            seo_rank_counter += 1
            seo_items.append({
                "rank": seo_rank_counter,
                "overall": idx,
                "store_id": store_id,
                "item_code": item_code,
            })

    return ad_items, seo_items


def find_self_rank(items: list[dict], target_code: str) -> int | None:
    """枠内アイテムリストから自店舗(yukaiya)の最上位順位を返す"""
    for item in items:
        if item["store_id"] == STORE_ID and item["item_code"] == target_code:
            return item["rank"]
    return None


# ==== git push ====

# pushが他者の更新と衝突した時の最大リトライ回数。
# Ycheck側のGitHub連携(自動保存)が同時刻に走るケースに備える。
GIT_PUSH_MAX_RETRY = 3
# pull --rebase 後のpush再試行までの待機秒数(リトライごとにこれを倍にする)
GIT_PUSH_RETRY_WAIT_SEC = 2


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """gitコマンド共通ラッパー"""
    return subprocess.run(["git"] + args, cwd=cwd, check=check, capture_output=True)


def _is_push_rejected(stderr_bytes: bytes) -> bool:
    """git push の失敗ログから「remote先行による rejected」かを判定する。

    典型的なメッセージ:
      ! [rejected]        main -> main (fetch first)
      error: failed to push some refs to 'https://...'
    これらが含まれていれば「単純なrejected = pull-rebaseで解決可能」と判断する。
    """
    if not stderr_bytes:
        return False
    s = stderr_bytes.decode("utf-8", errors="replace")
    if "[rejected]" in s and "fetch first" in s:
        return True
    if "non-fast-forward" in s:
        return True
    return False


def git_commit_and_push(repo_root: Path) -> bool:
    """rank.json を commit & push する。成功時 True

    【v9.3】タスクスケジューラ運用に耐える恒久対策:
      Ycheck画面のGitHub連携機能(自動保存)が同時刻にcommit & pushを行うため、
      手元PCのpushが [rejected] になるケースが頻発する。
      これを自動で解消するため、push失敗時は以下の手順で復旧を試みる:
        1. push失敗(rejected)を検知
        2. git pull --rebase で remote の最新コミットを取り込む
        3. 再度 push を試みる
        4. 1〜3を最大 GIT_PUSH_MAX_RETRY 回まで繰り返す
      これにより、ユーザーが手動で stash → pull → push → pop する必要がなくなる。

    【v9で修正】
      旧版は `git diff --quiet rank.json` で差分判定していたが、これは
      新規ファイルや stage 済みの変更を検知できない問題があった。
      新版は `git add` → `git diff --cached --quiet` で stage 後の差分を判定する。
    """
    try:
        # ── ステップ1: add ──
        _git(["add", "rank.json"], cwd=repo_root)

        # ── ステップ2: stage 後の差分があるか確認 ──
        diff_result = _git(["diff", "--cached", "--quiet"], cwd=repo_root, check=False)
        if diff_result.returncode == 0:
            # stageに差分なし=変更なし
            log("git: rank.jsonに変更なし、commit不要")
            return True

        # ── ステップ3: commit ──
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        commit_msg = f"chore: update rank.json ({timestamp})"
        _git(["commit", "-m", commit_msg], cwd=repo_root)

        # ── ステップ4: push (失敗時は pull --rebase でリトライ) ──
        wait_sec = GIT_PUSH_RETRY_WAIT_SEC
        for attempt in range(1, GIT_PUSH_MAX_RETRY + 1):
            push_result = _git(["push"], cwd=repo_root, check=False)
            if push_result.returncode == 0:
                log(f"git: push成功 ({commit_msg})")
                return True

            # 失敗:rejected かどうか判定
            if not _is_push_rejected(push_result.stderr):
                # rejected以外のエラー(認証失敗・ネットワーク不通など)は早期リターン
                log(f"✖ git push 失敗(リトライ対象外): "
                    f"{push_result.stderr.decode('utf-8', errors='replace').strip()}")
                return False

            # rejected 検知:remote先行 → pull --rebase で取り込んで再push
            log(f"⚠ git push が rejected (試行 {attempt}/{GIT_PUSH_MAX_RETRY}): "
                f"remote が先行しています。pull --rebase で取り込んで再試行します")

            try:
                pull_result = _git(["pull", "--rebase"], cwd=repo_root, check=False)
                if pull_result.returncode != 0:
                    # rebase で競合した場合 → abort して中断(rank.json は手元PCに残る)
                    log(f"✖ git pull --rebase 失敗: "
                        f"{pull_result.stderr.decode('utf-8', errors='replace').strip()}")
                    log(f"  競合を解消するため git rebase --abort を実行します")
                    _git(["rebase", "--abort"], cwd=repo_root, check=False)
                    return False
                log(f"  ✓ remoteの更新を取り込みました")
            except Exception as e:
                log(f"✖ git pull --rebase で例外: {e}")
                return False

            # 少し待ってから再push(連続push競合の緩和)
            if attempt < GIT_PUSH_MAX_RETRY:
                time.sleep(wait_sec)
                wait_sec *= 2  # 指数バックオフ:2秒 → 4秒 → 8秒

        # ループを抜けた=最大試行回数に達しても成功せず
        log(f"✖ git push が {GIT_PUSH_MAX_RETRY} 回連続で rejected。"
            f"後で手動で git push を実行してください")
        return False

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
                # → 圏外(✕)とエラー(空白)を区別できる
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
