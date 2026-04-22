# Ycheck 順位取得システム セットアップ手順

## 📁 追加するファイル構成

```
ycheck/                             ← 既存リポジトリのルート
├── index.html                      ← 既存(改修あり)
├── rank.json                       ← 自動生成(コミットしない初期状態でOK)
├── data/
│   └── products.csv                ← 商品マスタ(手動更新)
├── scripts/
│   └── rank_check.py               ← 順位取得スクリプト
└── .github/
    └── workflows/
        └── rank-check.yml          ← 自動実行設定
```

---

## 🚀 セットアップ手順

### ステップ1:ファイルをリポジトリに配置

上記4ファイル(`data/products.csv`, `scripts/rank_check.py`, `.github/workflows/rank-check.yml`)をリポジトリに追加してコミット。

### ステップ2:Client IDをGitHub Secretsに登録

1. GitHubで `kaiyoshida0318/ycheck` リポジトリを開く
2. **Settings** → **Secrets and variables** → **Actions**
3. **New repository secret** をクリック
4. 以下を登録:
   - **Name**: `YAHOO_APP_ID`
   - **Secret**: Yahoo!デベロッパーネットワークで発行されたClient ID
5. **Add secret** をクリック

### ステップ3:商品マスタCSVを準備

`data/products.csv` を以下のフォーマットで作成:

```csv
code,keyword
cab-25-01,キャビネット 木製
cab-25-02,キャビネット スリム
...
```

- `code`: Ycheckに登録されている商品コード(小文字)
- `keyword`: メインKW(検索キーワード)
- 列名は `商品コード` / `メインKW` でもOK(自動判別)

### ステップ4:手動で動作確認

1. GitHubリポジトリの **Actions** タブを開く
2. 左の **Yahoo Rank Check** を選択
3. 右上の **Run workflow** ボタンをクリック
4. 実行ログを確認(253商品なら約5分で完了)
5. 完了後、リポジトリに `rank.json` が自動コミットされる

### ステップ5:定期実行の確認

- 次回は毎日 **JST 04:00** に自動実行される
- Actionsタブで実行履歴を確認可能

---

## 🖥 Ycheck側のJavaScript改修ポイント

### 1. 起動時に自動fetch

`index.html` の初期化処理に以下を追加:

```javascript
async function loadRankFromUrl() {
  try {
    const url = 'rank.json?t=' + Date.now();  // キャッシュバスター
    const res = await fetch(url);
    if (!res.ok) throw new Error('fetch failed: ' + res.status);
    const data = await res.json();
    mergeRankData(data);
    renderRankTables();  // 既存の描画関数
    updateLastUpdatedDisplay(data.meta?.last_updated);
  } catch (e) {
    console.warn('rank.json 読み込み失敗:', e);
  }
}

// 初期化時に呼ぶ
loadRankFromUrl();
```

### 2. JSONデータをYcheck内部形式に変換してマージ

```javascript
function mergeRankData(json) {
  // json は { "2026-04": { "code": { "ad": [...], "seo": [...] } }, "meta": {...} }
  if (!state.rank) state.rank = {};

  for (const [ymDash, products] of Object.entries(json)) {
    if (ymDash === 'meta') continue;

    // "2026-04" → "2026/04" に変換
    const ymSlash = ymDash.replace('-', '/');

    if (!state.rank[ymSlash]) state.rank[ymSlash] = {};

    for (const [rawCode, fields] of Object.entries(products)) {
      const code = rawCode.toLowerCase();
      if (!state.rank[ymSlash][code]) state.rank[ymSlash][code] = {};

      // "ad" → "adRank", "seo" → "seoRank" に変換
      if (fields.ad) {
        state.rank[ymSlash][code].adRank = fields.ad;
      }
      if (fields.seo) {
        state.rank[ymSlash][code].seoRank = fields.seo;
      }
    }
  }

  saveState();  // 既存のlocalStorage保存関数(あれば)
}
```

### 3. 🔄 手動再取得ボタン

ヘッダー付近に以下のようなボタンを追加:

```html
<button id="btn-refresh-rank">🔄 順位再取得</button>
<span id="rank-last-updated" class="text-muted"></span>
```

```javascript
document.getElementById('btn-refresh-rank').addEventListener('click', () => {
  loadRankFromUrl();
});

function updateLastUpdatedDisplay(iso) {
  const el = document.getElementById('rank-last-updated');
  if (!el || !iso) return;
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  el.textContent = `最終更新: ${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
```

### 4. 値の型チェック修正

既存の `isNaN(v)` でフィルタしている箇所を、文字列も受け入れるよう修正:

```javascript
// 修正前(例)
if (v === null || isNaN(v)) return '';

// 修正後
if (v === null || v === undefined || v === '') return '';
// 数値でも文字列でもそのまま表示
return String(v);
```

---

## 📊 生成される rank.json のサンプル

```json
{
  "2026-04": {
    "cab-25-01": {
      "ad":  [null, null, ..., "最上段◎", "最上段◎", null, ...],
      "seo": [null, null, ..., "-", "-", null, ...]
    },
    "cab-25-02": {
      "ad":  [null, ..., "下段×", ...],
      "seo": [null, ..., "上段-PRオプ○", ...]
    }
  },
  "meta": {
    "last_updated": "2026-04-22T04:12:33+09:00",
    "total_products": 253,
    "success_count": 240,
    "not_found_count": 10,
    "error_count": 3
  }
}
```

---

## 🔧 運用上の注意

- **商品マスタの更新**:新商品を追加した場合、`data/products.csv` をpushすれば次回実行から自動で対象に含まれる
- **コスト**:完全無料(GitHub Actions無料枠内、Yahoo API無料枠内)
- **所要時間**:253商品で約5分。APIの1クエリ/秒制限により、商品数が増えると線形に時間が増える
- **失敗時**:GitHub Actionsのメール通知が飛ぶ(デフォルト設定)
- **過去データ**:rank.jsonはGitに全期間分が蓄積されるため、削除しない限り履歴が残る
