#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yahoo!ショッピング 商品検索APIの広告/SEO判別フィールド検証スクリプト

目的:
  同一商品コードが複数回出現するケースを見つけ、それらのオブジェクト間に
  どのような差異があるかを全フィールドにわたって比較する。
  広告枠とSEO枠を判別する手がかりとなるフィールドを探すのが目的。

実行方法:
  python scripts/inspect_api_response.py

出力:
  1. 各キーワードのrawレスポンス(JSON) → /tmp/raw_response_{n}.json
  2. 同一商品の差異分析レポート → /tmp/inspection_report.md
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import requests

# ==== 設定 ====
APP_ID = os.environ.get("YAHOO_APP_ID")
STORE_ID = "yukaiya"
API_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
RESULTS_PER_PAGE = 50
REQUEST_INTERVAL = 1.5  # 検証なので余裕を持って
TIMEOUT = 20

# 検証対象のキーワード(同一商品が複数出現する可能性が高いキーワードを選定)
# 自店舗で売れ筋・上位表示されている可能性の高いものを優先
TEST_KEYWORDS = [
    "かかと 靴擦れ 防止",
    "オーラルb 替えブラシ",
    "スマートウォッチ ベルト",
    "fitbit charge5 バンド",
    "排水口 ゴミ受け",
    "傷防止 フェルト",
    "ヨガソックス",
    "コンセントカバー",
    "時計 ベルトループ",
    "蝶ネクタイ",
]

# 出力先
OUTPUT_DIR = Path("/tmp/yahoo_api_inspection")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JST = timezone(timedelta(hours=9))


def log(msg: str) -> None:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def search_items(keyword: str, results: int = 50) -> dict:
    """キーワードで商品検索。レスポンス全体を返す。"""
    params = {
        "appid": APP_ID,
        "query": keyword,
        "results": results,
        "start": 1,
        "sort": "-score",
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=TIMEOUT)
        if resp.status_code == 429:
            log("  ⚠ 429 Too Many Requests → 5秒待機してリトライ")
            time.sleep(5)
            resp = requests.get(API_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"  ✖ APIエラー (keyword='{keyword}'): {e}")
        return {}


def find_duplicate_products(hits: list[dict]) -> dict[str, list[tuple[int, dict]]]:
    """
    同一商品コードが複数出現するものを抽出。
    戻り値: {code: [(順位1, hit1), (順位2, hit2), ...]}
    """
    code_to_hits = defaultdict(list)
    for idx, hit in enumerate(hits, start=1):
        code = hit.get("code", "")
        if code:
            code_to_hits[code].append((idx, hit))
    # 2件以上のものだけ
    return {code: lst for code, lst in code_to_hits.items() if len(lst) >= 2}


def compare_dicts(d1: dict, d2: dict, path: str = "") -> list[dict]:
    """
    2つの辞書を再帰的に比較し、差異を返す。
    戻り値: [{path, value1, value2, type}]
    """
    differences = []
    all_keys = set(d1.keys()) | set(d2.keys())

    for key in sorted(all_keys):
        current_path = f"{path}.{key}" if path else key
        v1 = d1.get(key, "<MISSING>")
        v2 = d2.get(key, "<MISSING>")

        if isinstance(v1, dict) and isinstance(v2, dict):
            differences.extend(compare_dicts(v1, v2, current_path))
        elif isinstance(v1, list) and isinstance(v2, list):
            if v1 != v2:
                differences.append({
                    "path": current_path,
                    "value1": v1,
                    "value2": v2,
                    "type": "list_diff"
                })
        elif v1 != v2:
            differences.append({
                "path": current_path,
                "value1": v1,
                "value2": v2,
                "type": "value_diff"
            })

    return differences


def get_all_top_level_keys(hits: list[dict]) -> set[str]:
    """全hitsから出現するトップレベルキーをすべて抽出(undocumentedフィールド検出用)"""
    keys = set()
    for hit in hits:
        keys.update(hit.keys())
    return keys


# 公式ドキュメントに記載されているトップレベルフィールド
DOCUMENTED_FIELDS = {
    "index", "name", "description", "headLine", "inStock", "url", "code",
    "condition", "taxExcludePrice", "taxExcludePremiumPrice", "premiumPrice",
    "premiumPriceStatus", "premiumDiscountType", "premiumDiscountRate",
    "imageId", "image", "exImage", "review", "affiliateRate", "price",
    "priceLabel", "point", "shipping", "genreCategory", "parentGenreCategories",
    "brand", "parentBrands", "janCode", "payment", "releaseDate", "seller",
    "delivery"
}


def main() -> int:
    if not APP_ID:
        log("✖ 環境変数 YAHOO_APP_ID が設定されていません")
        return 1

    log("=" * 60)
    log("Yahoo APIレスポンス検証スクリプト 開始")
    log("=" * 60)

    report_lines = []
    report_lines.append("# Yahoo!ショッピング商品検索API 検証レポート")
    report_lines.append("")
    report_lines.append(f"実行日時: {datetime.now(JST).isoformat()}")
    report_lines.append(f"検証キーワード数: {len(TEST_KEYWORDS)}")
    report_lines.append("")

    all_undocumented_fields = set()
    duplicate_cases_total = 0
    keywords_with_dups = 0

    for kw_idx, keyword in enumerate(TEST_KEYWORDS, start=1):
        log(f"\n[{kw_idx}/{len(TEST_KEYWORDS)}] キーワード検索: '{keyword}'")
        report_lines.append(f"## キーワード: `{keyword}`")
        report_lines.append("")

        response = search_items(keyword)
        if not response:
            log(f"  ✖ レスポンス空")
            report_lines.append("- レスポンス取得失敗")
            report_lines.append("")
            time.sleep(REQUEST_INTERVAL)
            continue

        # rawレスポンスを保存
        raw_path = OUTPUT_DIR / f"raw_response_{kw_idx:02d}_{keyword.replace(' ', '_')}.json"
        with raw_path.open("w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        log(f"  ✓ rawレスポンス保存: {raw_path.name}")

        hits = response.get("hits", [])
        log(f"  取得件数: {len(hits)}件")
        report_lines.append(f"- 取得件数: {len(hits)}件")

        # 1. undocumented フィールドの検出
        all_keys = get_all_top_level_keys(hits)
        undocumented = all_keys - DOCUMENTED_FIELDS
        if undocumented:
            log(f"  🔍 undocumentedフィールド発見: {undocumented}")
            report_lines.append(f"- **🔍 undocumentedフィールド**: `{', '.join(sorted(undocumented))}`")
            all_undocumented_fields.update(undocumented)
        else:
            report_lines.append("- undocumentedフィールド: なし")

        # 2. 同一商品コードの重複検出
        duplicates = find_duplicate_products(hits)
        log(f"  重複商品コード数: {len(duplicates)}件")
        report_lines.append(f"- 同一商品コードの重複: {len(duplicates)}件")
        report_lines.append("")

        if not duplicates:
            report_lines.append("(重複商品なしのため差異分析スキップ)")
            report_lines.append("")
            time.sleep(REQUEST_INTERVAL)
            continue

        keywords_with_dups += 1

        # 3. 重複商品の差異分析
        for code, occurrences in duplicates.items():
            duplicate_cases_total += 1
            log(f"  📦 重複商品: {code} (出現位置: {[idx for idx, _ in occurrences]})")
            report_lines.append(f"### 重複商品: `{code}`")
            ranks_str = " / ".join(f"{idx}位" for idx, _ in occurrences)
            report_lines.append(f"- 出現位置: {ranks_str}")
            report_lines.append("")

            # 最初の2件を比較(3件以上なら最初の2件のみ)
            (idx1, hit1), (idx2, hit2) = occurrences[0], occurrences[1]
            differences = compare_dicts(hit1, hit2)

            # indexフィールドの差は当然なので除外して件数カウント
            meaningful_diffs = [d for d in differences if d["path"] != "index"]

            log(f"     差異: indexを除いて {len(meaningful_diffs)}項目")
            report_lines.append(f"#### {idx1}位 vs {idx2}位 の差異(index除く): {len(meaningful_diffs)}項目")
            report_lines.append("")

            if meaningful_diffs:
                report_lines.append("| フィールドパス | " + f"{idx1}位の値" + " | " + f"{idx2}位の値" + " |")
                report_lines.append("|---|---|---|")
                for diff in meaningful_diffs:
                    v1_str = json.dumps(diff["value1"], ensure_ascii=False)[:80]
                    v2_str = json.dumps(diff["value2"], ensure_ascii=False)[:80]
                    # マークダウンのテーブルでパイプ文字をエスケープ
                    v1_str = v1_str.replace("|", "\\|")
                    v2_str = v2_str.replace("|", "\\|")
                    report_lines.append(f"| `{diff['path']}` | {v1_str} | {v2_str} |")
                report_lines.append("")
            else:
                report_lines.append("⚠️ **完全に同一(indexを除く)** → APIレベルでは判別不可能")
                report_lines.append("")

        time.sleep(REQUEST_INTERVAL)

    # サマリー
    report_lines.insert(4, "## 📊 サマリー")
    report_lines.insert(5, "")
    report_lines.insert(6, f"- 重複商品が見つかったキーワード数: {keywords_with_dups}/{len(TEST_KEYWORDS)}")
    report_lines.insert(7, f"- 重複ケース総数: {duplicate_cases_total}")
    if all_undocumented_fields:
        report_lines.insert(8, f"- 全体で発見されたundocumentedフィールド: `{', '.join(sorted(all_undocumented_fields))}`")
    else:
        report_lines.insert(8, "- 全体で発見されたundocumentedフィールド: なし")
    report_lines.insert(9, "")

    # レポート保存
    report_path = OUTPUT_DIR / "inspection_report.md"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    log(f"\n✓ 検証レポート保存: {report_path}")

    # 結論サマリーをコンソールにも出力
    log("\n" + "=" * 60)
    log("【検証結果サマリー】")
    log("=" * 60)
    log(f"重複商品が見つかったキーワード: {keywords_with_dups}/{len(TEST_KEYWORDS)}")
    log(f"重複ケース総数: {duplicate_cases_total}")
    log(f"undocumentedフィールド: {sorted(all_undocumented_fields) if all_undocumented_fields else 'なし'}")
    log(f"\n📁 詳細レポート: {report_path}")
    log(f"📁 rawレスポンス: {OUTPUT_DIR}/raw_response_*.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
