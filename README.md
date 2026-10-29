# Makimono 電子ラボノート データ可視化・分析 GUI

Makimono（https://makimono.elab.one）に溜まった実験データを API で取得し、
ブラウザ上でその場で絞り込み・可視化・統計分析できる Streamlit アプリです。
CSV / Excel / JSON ファイルの読み込みにも対応しているので、エクスポート済みデータの分析にも使えます。

## 起動方法

Python 3.10 以上が必要です。

```bash
./run.sh          # macOS / Linux
run.bat           # Windows
```

初回は仮想環境の作成と依存パッケージのインストールが走ります。
起動後、ブラウザで http://localhost:8501 が開きます。

手動で起動する場合:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 接続設定（.env）

`.env` に接続先と API キーを書いておくと、起動時に自動で読み込まれます（`.env.example` 参照）。

| 変数 | 意味 |
|---|---|
| `MAKIMONO_BASE_URL` | ベース URL（既定: `https://makimono.elab.one`） |
| `MAKIMONO_API_KEY` | API キー |
| `MAKIMONO_AUTH_MODE` | `header` / `bearer` / `query` のいずれか |
| `MAKIMONO_AUTH_HEADER` | `header` のときのヘッダ名（例: `X-API-Key`） |
| `MAKIMONO_AUTH_PARAM` | `query` のときのパラメータ名（例: `api_key`） |
| `MAKIMONO_ENDPOINT` | 既定で開くエンドポイント |

`.env` は `.gitignore` 済みです。API キーをリポジトリに入れないでください。

## API 仕様がわからないとき

1. サイドバー「🔍 エンドポイント探索」で、よくあるパスに一括アクセスして JSON を返すものを探せます。
2. より詳しく調べるには、同梱の探索スクリプトを実行してください。

   ```bash
   python discover_api.py            # .env の設定で実行
   python discover_api.py --save-raw # HTML / JS も保存
   ```

   `dashboard.php` と読み込まれている JavaScript を解析して API の手がかりを抽出し、
   代表的なエンドポイントを複数の認証方式で試して `discovery_report.json` に保存します
   （API キーは含まれません）。このファイルをもとにアプリ側の接続設定を調整できます。

## 画面の構成

| タブ | 内容 |
|---|---|
| 📋 概要 | 行数・列数・期間、列ごとのプロファイル（型、欠損、ユニーク数、最小/最大/平均）、数値列の推移（スモールマルチプル）、欠損率 |
| 🔎 データ・絞り込み | 全文検索、期間、カテゴリ、数値範囲での絞り込み。ここでの絞り込みは全タブに反映 |
| 📈 可視化 | 折れ線・散布図・棒・箱ひげ・バイオリン・ヒストグラム・面・ヒートマップ。色分け、ファセット、集計、対数軸、回帰直線。チャートの保存と HTML 出力 |
| 📊 統計分析 | 基本統計量、グループ集計（日時は日/週/月/四半期/年）、相関行列、回帰・トレンド（OLS）、群間比較（Welch t / Mann-Whitney / ANOVA / Kruskal-Wallis）、外れ値検出（IQR / Z スコア） |
| ⬇️ エクスポート | 絞り込み後データを CSV / Excel / JSON で保存、API 生レスポンスの保存 |

## データの自動整形

API から返る JSON は入れ子になっていることが多いため、次の処理を自動で行います。

- JSON の中から「表」になりそうなレコード配列を検出（複数あれば選択可能）
- 入れ子のキーを `親.子` 形式の列に展開
- `"25 ℃"`, `"1,234 mg"`, `"45%"` のような単位付き文字列を数値列 + `<列名>_unit` 列に分離
- 日付らしい文字列を日時型に変換（列名のヒントと内容の両方で判定）

## 構成

```
app.py                  Streamlit アプリ本体
makimono/api_client.py  認証方式・ページネーションを設定可能な API クライアント
makimono/data_utils.py  JSON → DataFrame 変換、型推定、ファイル読込
makimono/charts.py      Plotly チャート（配色・スタイル統一）
discover_api.py         API 仕様探索スクリプト
sample_data/            動作確認用の合成データと生成スクリプト
tests/                  pytest（python -m pytest tests）
```
