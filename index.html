#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yahoo!ショッピング 商品検索APIを使って、自店舗商品の広告順位・SEO順位を取得。
結果を rank.json に追記保存する。

判定ルール:
  1～4位 → 最上位:広告「上段\n◎」/ 2番目以降(同一code):SEO欄にも記録
           1件のみの場合はSEO「-」
  5～10位 → 広告「下段\n×」/ SEO「上段\nPR○」
  11位～  → 広告「下段\n×」/ SEO「下段\nPR×」
  圏外(50位以内にヒットなし) → 広告「-」/ SEO「-」
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ==== 設定 ====
APP_ID = os.environ.get("YAHOO_APP_ID")
STORE_ID = "yukaiya"
API_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
RESULTS_PER_PAGE = 50  # 1リクエストで取得する件数(最大50)
REQUEST_INTERVAL = 1.1  # API制限(1クエリ/秒)対策。安全マージン込み
TIMEOUT = 15

# ==== パス設定 ====
ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_CSV = ROOT / "data" / "products.csv"
RANK_JSON = ROOT / "rank.json"  # GitHub Pages直下に配置

JST = timezone(timedelta(hours=9))


def log(msg: str) -> None:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def load_products(csv_path: Path) -> list[dict]:
    """商品マスタCSVを読み込む。列: code, keyword"""
    products = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("code") or row.get("商品コード") or "").strip().lower()
            keyword = (row.get("keyword") or row.get("メインKW") or "").strip()
            if code and keyword:
                products.append({"code": code, "keyword": keyword})
    return products


def search_items(keyword: str) -> list[dict]:
    """キーワードで商品検索。上位50件のhitsリストを返す。"""
    params = {
        "appid": APP_ID,
        "query": keyword,
        "results": RESULTS_PER_PAGE,
        "start": 1,
        "sort": "-score",  # おすすめ順(デフォルト。検索順位と同等)
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=TIMEOUT)
        if resp.status_code == 429:
            log(f"  ⚠ 429 Too Many Requests (keyword='{keyword}') 5秒待機してリトライ")
            time.sleep(5)
            resp = requests.get(API_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", []) or []
    except Exception as e:
        log(f"  ✖ APIエラー (keyword='{keyword}'): {e}")
        return []


def extract_shop_code(api_code: str) -> str:
    """
    APIレスポンスの code ('yukaiya_cab-25-01' 形式) から
    ストアIDプレフィックスを除去し、小文字化して返す。
    """
    if not api_code:
        return ""
    prefix = f"{STORE_ID}_"
    code = api_code[len(prefix):] if api_code.startswith(prefix) else api_code
    return code.strip().lower()


def find_self_ranks(hits: list[dict], target_code: str) -> list[int]:
    """
    検索結果から自店舗の target_code と一致する商品の順位(1始まり)を
    全て返す(同一商品が複数表示された場合に備えて複数)。
    """
    ranks = []
    for idx, hit in enumerate(hits, start=1):
        seller_id = (hit.get("seller") or {}).get("sellerId", "")
        if seller_id != STORE_ID:
            continue
        shop_code = extract_shop_code(hit.get("code", ""))
        if shop_code == target_code:
            ranks.append(idx)
    return ranks


def judge_ranks(ranks: list[int]) -> tuple[str, str]:
    """
    順位リスト(昇順)から広告・SEOの判定文字列を返す。
    戻り値: (ad_value, seo_value)
    """
    if not ranks:
        # 圏外
        return "-", "-"

    ranks = sorted(set(ranks))
    top = ranks[0]

    if 1 <= top <= 4:
        # 最上段PR枠確定
        # 2番目以降に同一codeがあれば、その順位によってSEO側も判定
        ad_value = "上段\n◎"
        if len(ranks) >= 2:
            second = ranks[1]
            if 5 <= second <= 10:
                seo_value = "上段\nPR○"
            elif second >= 11:
                seo_value = "下段\nPR×"
            else:
                # 2番目も1-4位内(理論上ほぼ起きないが安全側で)
                seo_value = "-"
        else:
            seo_value = "-"
        return ad_value, seo_value

    if 5 <= top <= 10:
        return "下段\n×", "上段\nPR○"

    # 11位以上
    return "下段\n×", "下段\nPR×"


def load_existing_rank_json(path: Path) -> dict:
    """既存のrank.jsonを読み込む。なければ空の構造で初期化。"""
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠ rank.json読み込み失敗: {e} → 新規作成します")
    return {}


def days_in_month(year: int, month: int) -> int:
    """指定年月の日数を返す。"""
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day.day


def ensure_month_slot(data: dict, ym: str, year: int, month: int) -> None:
    """月のデータ構造を準備(配列サイズ=その月の日数)。"""
    if ym not in data:
        data[ym] = {}


def set_today_value(
    data: dict,
    ym: str,
    code: str,
    field: str,
    day_index: int,
    days: int,
    value,
) -> None:
    """指定日の値をセット。配列は月の日数分確保。"""
    if code not in data[ym]:
        data[ym][code] = {}
    if field not in data[ym][code]:
        data[ym][code][field] = [None] * days
    # 既存配列が日数より短ければ拡張
    if len(data[ym][code][field]) < days:
        data[ym][code][field].extend([None] * (days - len(data[ym][code][field])))
    data[ym][code][field][day_index] = value


def main() -> int:
    if not APP_ID:
        log("✖ 環境変数 YAHOO_APP_ID が設定されていません")
        return 1

    if not PRODUCTS_CSV.exists():
        log(f"✖ 商品マスタが見つかりません: {PRODUCTS_CSV}")
        return 1

    # 実行日時(JST)
    now_jst = datetime.now(JST)
    ym = now_jst.strftime("%Y-%m")  # 例: "2026-04"
    year, month, day = now_jst.year, now_jst.month, now_jst.day
    day_index = day - 1  # 配列は0始まり
    total_days = days_in_month(year, month)

    log(f"=== 順位取得開始: {ym} / 日={day} (index={day_index}) ===")

    products = load_products(PRODUCTS_CSV)
    log(f"対象商品数: {len(products)}件")

    if not products:
        log("✖ 対象商品が0件です")
        return 1

    # 既存データ読み込み
    rank_data = load_existing_rank_json(RANK_JSON)
    ensure_month_slot(rank_data, ym, year, month)

    success = 0
    not_found = 0
    errors = 0

    for i, product in enumerate(products, start=1):
        code = product["code"]
        keyword = product["keyword"]

        if not keyword:
            log(f"[{i}/{len(products)}] {code}: キーワード空 → スキップ")
            errors += 1
            continue

        hits = search_items(keyword)
        if not hits:
            log(f"[{i}/{len(products)}] {code} (KW='{keyword}'): API結果0件")
            ranks = []
            errors += 1
        else:
            ranks = find_self_ranks(hits, code)

        ad_value, seo_value = judge_ranks(ranks)

        set_today_value(rank_data, ym, code, "ad", day_index, total_days, ad_value)
        set_today_value(rank_data, ym, code, "seo", day_index, total_days, seo_value)

        if ranks:
            log(f"[{i}/{len(products)}] {code}: 順位={ranks} → 広告='{ad_value!r}' SEO='{seo_value!r}'")
            success += 1
        else:
            not_found += 1

        # API制限対策(1クエリ/秒)
        time.sleep(REQUEST_INTERVAL)

    # meta情報を更新
    rank_data["meta"] = {
        "last_updated": now_jst.isoformat(),
        "total_products": len(products),
        "success_count": success,
        "not_found_count": not_found,
        "error_count": errors,
    }

    # 保存
    RANK_JSON.parent.mkdir(parents=True, exist_ok=True)
    with RANK_JSON.open("w", encoding="utf-8") as f:
        json.dump(rank_data, f, ensure_ascii=False, indent=2)

    log(f"=== 完了 === 成功:{success} 圏外:{not_found} エラー:{errors}")
    log(f"保存先: {RANK_JSON}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
