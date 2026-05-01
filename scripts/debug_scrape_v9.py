#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
デバッグ用 v9:商品コンテナ本体のみを正しく抽出するセレクタ修正版

【v8からの主な変更】
- v8の問題:[class*="SearchResult_SearchResultItem_"] が部分一致のため、
  本体だけでなく子要素(__price, __image, __imageIcon等)もカウントしていた。
  結果、最初の30要素には本体3個分の子要素まで混在し、画像URLが取れず「?」だらけになっていた。
- v9の修正:本体クラス名は「SearchResult_SearchResultItem__<ハッシュ>」(__がちょうど1つ)
  というパターンを満たすことを利用し、JS側で正規表現で本体のみフィルタする。

【使い方】
  python debug_scrape_v9.py "キーワード1" "キーワード2" ...
  例:python debug_scrape_v9.py "かかと 靴擦れ 防止" "オーラルb 替えブラシ" "スマートウォッチ ベルト"
"""

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

CODE_PATTERN = re.compile(r'/i/j/([\w\-]+?)_([\w\-]+?)(?:[?&]|$)')
STORE_URL_PATTERN = re.compile(r'store\.shopping\.yahoo\.co\.jp/([\w\-]+)/([\w\-]+?)(?:\.html|/|$|\?)')
TARGET_STORE = "yukaiya"


async def debug_keyword(context, keyword: str, save_html: bool = False):
    encoded = quote_plus(keyword)
    url = f"https://shopping.yahoo.co.jp/search?p={encoded}"
    print(f"\n{'=' * 100}")
    print(f"検索KW: {keyword}")
    print(f"URL: {url}")
    print(f"{'=' * 100}")

    page = await context.new_page()
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # 本体セレクタが現れるまで待つ
        await page.wait_for_selector('[class*="SearchResult_SearchResultItem_"]', timeout=15000)
        await page.wait_for_timeout(2000)

        # スクロール(画像遅延ロード対策で念のため)
        for _ in range(15):
            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(400)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1500)

        # HTML保存(検証用)
        if save_html:
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            html_path = log_dir / f"debug_v9_{re.sub(r'[^a-zA-Z0-9]+', '_', keyword)[:40]}.html"
            html = await page.content()
            html_path.write_text(html, encoding="utf-8")
            print(f"HTML保存: {html_path} ({len(html):,}文字)")

        # JS側で本体コンテナのみを正規表現抽出
        # 本体: SearchResult_SearchResultItem__<ハッシュ>(__は1回)
        # 子要素: SearchResult_SearchResultItem__<名前>__<ハッシュ>(__は2回以上)
        items_data = await page.evaluate("""
            () => {
                const all = document.querySelectorAll('[class*="SearchResult_SearchResultItem"]');
                const mainItems = [];
                for (const el of all) {
                    const classes = (el.getAttribute('class') || '').split(/\\s+/);
                    let isMain = false;
                    for (const c of classes) {
                        if (c.startsWith('SearchResult_SearchResultItem__')) {
                            // __ の出現回数で本体か子要素かを判定
                            const underscoreCount = (c.match(/__/g) || []).length;
                            if (underscoreCount === 1) {
                                isMain = true;
                                break;
                            }
                        }
                    }
                    if (isMain) mainItems.push(el);
                }
                return mainItems.slice(0, 30).map((item, idx) => {
                    const pr = item.querySelector('[class*="imageIcon--pr"]');
                    const img = item.querySelector('img');
                    const src = img ? (img.getAttribute('src') || '') : '';
                    const dataSrc = img ? (img.getAttribute('data-src') || '') : '';
                    const srcset = img ? (img.getAttribute('srcset') || '') : '';
                    const links = item.querySelectorAll('a[href]');
                    const allHrefs = Array.from(links).map(a => a.getAttribute('href') || '').filter(h => h);
                    const textContent = item.textContent || '';
                    return {
                        index: idx + 1,
                        is_ad: !!pr,
                        src, dataSrc, srcset,
                        hrefs: allHrefs,
                        textContent,
                    };
                });
            }
        """)

        print(f"\n本体コンテナ取得数: {len(items_data)}件")
        print(f"\n{'位':>3}  {'枠内':>5}  {'PR':<3} {'★':<2} {'店':<22} {'コード':<28} {'方法':<12}")
        print("-" * 95)

        ad_rank = 0
        seo_rank = 0
        yukaiya_items = []

        for d in items_data:
            idx = d["index"]
            is_ad = d["is_ad"]

            store, code, method = "?", "?", ""

            # 方法1:画像URLから抽出
            for s in [d["src"], d["dataSrc"], d["srcset"]]:
                if not s:
                    continue
                m = CODE_PATTERN.search(s)
                if m:
                    store, code = m.group(1), m.group(2)
                    method = "画像URL"
                    break

            # 方法2:hrefから抽出(画像URL未取得時のフォールバック)
            if code == "?":
                for href in d["hrefs"]:
                    m = STORE_URL_PATTERN.search(href)
                    if m:
                        store, code = m.group(1), m.group(2)
                        method = "href"
                        break

            # 方法3:テキストに「ゆかい屋」(コード不明でも検出)
            if store != TARGET_STORE and TARGET_STORE != "ゆかい屋":  # 単純比較不可なので
                pass
            if "ゆかい屋" in d["textContent"] and store != "yukaiya":
                # 既にコード取れてるが店違う、もしくはコード?の場合
                if store == "?":
                    store = "yukaiya"
                    method = "テキスト判定"

            if is_ad:
                ad_rank += 1
                rank_label = f"AD{ad_rank}"
            else:
                seo_rank += 1
                rank_label = f"SEO{seo_rank}"

            ad_mark = "PR" if is_ad else "  "
            yk_mark = "★" if (store == "yukaiya" or "ゆかい屋" in d["textContent"]) else "  "

            print(f"{idx:>3}位  {rank_label:>5}  {ad_mark:<3} {yk_mark:<2} "
                  f"{store[:22]:<22} {code[:28]:<28} {method:<12}")

            if store == "yukaiya" or "ゆかい屋" in d["textContent"]:
                yukaiya_items.append({
                    "overall": idx,
                    "is_ad": is_ad,
                    "ad_rank": ad_rank if is_ad else None,
                    "seo_rank": seo_rank if not is_ad else None,
                    "code": code,
                    "method": method,
                })

        print(f"\n{'-' * 95}")
        print(f"ゆかい屋検出: {len(yukaiya_items)}件")
        for item in yukaiya_items:
            kind = "広告枠" if item["is_ad"] else "SEO枠"
            in_rank = item["ad_rank"] if item["is_ad"] else item["seo_rank"]
            print(f"  全体{item['overall']:>2}位 / {kind}{in_rank}位: code={item['code']:<25} ({item['method']})")

        # 取得失敗(?)の数
        failed = sum(1 for d in items_data if "?" in [d.get("src", "")[:1]])
        no_code = []
        for i, d in enumerate(items_data, start=1):
            has_code = False
            for s in [d["src"], d["dataSrc"], d["srcset"]]:
                if s and CODE_PATTERN.search(s):
                    has_code = True
                    break
            if not has_code:
                # hrefでも取れないか
                for h in d["hrefs"]:
                    if STORE_URL_PATTERN.search(h):
                        has_code = True
                        break
            if not has_code:
                no_code.append(i)
        print(f"コード取得失敗: {len(no_code)}件 {no_code if no_code else ''}")

    finally:
        await page.close()


async def main_async(keywords: list[str]):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
            viewport={"width": 1280, "height": 900},
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        for i, kw in enumerate(keywords):
            # 1キーワード目だけHTML保存(検証用)
            await debug_keyword(context, kw, save_html=(i == 0))
            if i < len(keywords) - 1:
                print("\n(8秒待機)")
                await asyncio.sleep(8)

        await browser.close()


def main():
    if len(sys.argv) < 2:
        print('使い方: python debug_scrape_v9.py "キーワード1" ["キーワード2" ...]')
        print('例: python debug_scrape_v9.py "かかと 靴擦れ 防止" "オーラルb 替えブラシ" "スマートウォッチ ベルト"')
        sys.exit(1)

    keywords = sys.argv[1:]
    asyncio.run(main_async(keywords))


if __name__ == "__main__":
    main()
