"""Makimono 電子ラボノート データ可視化・分析 GUI (Streamlit)

起動:  streamlit run app.py
"""
from __future__ import annotations

import io
import json
import os
from datetime import date

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from makimono import charts
from makimono.api_client import COMMON_ENDPOINTS, MakimonoClient, diagnose
from makimono.data_utils import (
    coerce_types,
    column_roles,
    extract_records,
    find_record_arrays,
    load_file,
    rank_numeric,
    records_to_df,
    summarize,
)

load_dotenv()


def _cfg(key: str, default: str = "") -> str:
    """設定値を .env / 環境変数 / Streamlit Cloud の Secrets の順に探す。"""
    val = os.getenv(key)
    if val:
        return val
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # noqa: BLE001  secrets.toml が無い環境
        pass
    return default

st.set_page_config(page_title="Makimono データ分析", page_icon="🧪", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
      .block-container {padding-top: 3.2rem; padding-bottom: 2rem;}
      div[data-testid="stMetric"] {background: #fcfcfb; border: 1px solid #e6e5e1; border-radius: 8px; padding: 10px 14px;}
      div[data-testid="stMetric"] label {color: #52514e;}
      .small-note {color:#52514e; font-size: 0.85rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_data", "sample_experiments.csv")


# ============================================================== state helpers
def _init_state() -> None:
    st.session_state.setdefault("raw_json", None)
    st.session_state.setdefault("raw_df", None)
    st.session_state.setdefault("source_label", "")
    st.session_state.setdefault("probe_results", None)
    st.session_state.setdefault("saved_charts", [])


def set_dataframe(df: pd.DataFrame, label: str) -> None:
    st.session_state["raw_df"] = coerce_types(df)
    st.session_state["source_label"] = label


_init_state()


# ==================================================================== sidebar
with st.sidebar:
    st.title("🧪 Makimono 分析")
    st.caption("電子ラボノートのデータを取得して、その場で可視化・分析")
    source = st.radio("データソース", ["Makimono API", "ファイルアップロード", "サンプルデータ"], index=0)

    # ---------------------------------------------------------- API source
    if source == "Makimono API":
        with st.expander("接続設定", expanded=True):
            base_url = st.text_input("ベース URL", _cfg("MAKIMONO_BASE_URL", "https://makimono.elab.one"))
            api_key = st.text_input("API キー", _cfg("MAKIMONO_API_KEY", ""), type="password")
            auth_mode = st.selectbox(
                "認証方式",
                ["header", "bearer", "query", "none"],
                index=["header", "bearer", "query", "none"].index(_cfg("MAKIMONO_AUTH_MODE", "header")),
                help="header: 任意ヘッダにキーを載せる / bearer: Authorization: Bearer / query: URL パラメータ",
            )
            header_name = st.text_input("ヘッダ名", _cfg("MAKIMONO_AUTH_HEADER", "X-API-Key"), disabled=auth_mode != "header")
            param_name = st.text_input("パラメータ名", _cfg("MAKIMONO_AUTH_PARAM", "api_key"), disabled=auth_mode != "query")
            verify_ssl = st.checkbox("SSL 証明書を検証", value=True)

        client = MakimonoClient(
            base_url=base_url,
            api_key=api_key,
            auth_mode=auth_mode,
            header_name=header_name,
            param_name=param_name,
            verify_ssl=verify_ssl,
        )

        with st.expander("データ取得", expanded=True):
            endpoint = st.text_input("エンドポイント", _cfg("MAKIMONO_ENDPOINT", "/api.php"), help="ベース URL からの相対パス、または完全な URL")
            method = st.selectbox("メソッド", ["GET", "POST"], index=0)
            params_text = st.text_area("クエリ / ボディ (JSON)", "{}", height=70, help='例: {"action": "list", "limit": 100}')
            paginate = st.checkbox("ページネーションを辿る", value=False)
            page_param = st.text_input("ページ番号パラメータ", "page", disabled=not paginate)
            max_pages = st.number_input("最大ページ数", 1, 1000, 50, disabled=not paginate)
            fetch_clicked = st.button("📥 取得", type="primary", use_container_width=True)

        if fetch_clicked:
            try:
                extra = json.loads(params_text or "{}")
                if not isinstance(extra, dict):
                    raise ValueError("JSON オブジェクトを指定してください")
            except (json.JSONDecodeError, ValueError) as exc:
                st.error(f"パラメータ JSON が不正です: {exc}")
                extra = None
            if extra is not None:
                with st.spinner("取得中…"):
                    if paginate:
                        records, responses = client.fetch_all(
                            endpoint,
                            method,
                            params=extra if method == "GET" else None,
                            json_body=extra if method == "POST" else None,
                            page_param=page_param,
                            max_pages=int(max_pages),
                        )
                        last = responses[-1] if responses else None
                        if last and not last.ok and len(responses) == 1:
                            st.error(f"HTTP {last.status}: {last.preview(200)}")
                        else:
                            st.session_state["raw_json"] = records
                            st.success(f"{len(records)} 件 / {len(responses)} ページ取得")
                    else:
                        resp = client.request(
                            endpoint,
                            method,
                            params=extra if method == "GET" else None,
                            json_body=extra if method == "POST" else None,
                        )
                        if not resp.ok:
                            st.error(f"HTTP {resp.status} ({resp.elapsed_ms} ms): {resp.preview(300)}")
                        elif not resp.is_json:
                            st.warning("JSON ではない応答です（HTML の可能性）。下のプレビューを確認してください。")
                            st.code(resp.preview(1500), language="html")
                        else:
                            st.session_state["raw_json"] = resp.body
                            st.success(f"HTTP {resp.status} ({resp.elapsed_ms} ms)")

        raw = st.session_state.get("raw_json")
        if raw is not None:
            with st.expander("レコード配列の選択", expanded=True):
                cands = find_record_arrays(raw) if not isinstance(raw, list) else [("$", len(raw), [])]
                if not cands:
                    cands = [("$", 1, [])]
                labels = [f"{p}  ({n} 件)" for p, n, _ in cands]
                choice = st.selectbox("JSON 内のテーブル候補", labels, index=0)
                chosen_path = cands[labels.index(choice)][0]
                if st.button("この配列を表に展開", use_container_width=True):
                    recs, used = extract_records(raw, chosen_path)
                    df = records_to_df(recs)
                    set_dataframe(df, f"API {endpoint} [{used}]")
                    st.success(f"{len(df)} 行 × {df.shape[1]} 列")
                with st.popover("生 JSON を見る"):
                    st.json(raw if len(json.dumps(raw, default=str)) < 200_000 else {"note": "大きすぎるため先頭のみ", "head": str(raw)[:5000]}, expanded=False)

        with st.expander("🩺 接続診断", expanded=False):
            st.caption("「Connection reset」や「timeout」が出るときに、DNS → TCP → TLS → HTTP のどこで止まるかを調べます。")
            if st.button("診断を実行", use_container_width=True):
                with st.spinner("診断中…"):
                    steps = diagnose(base_url)
                st.dataframe(pd.DataFrame(steps), use_container_width=True, hide_index=True)
                ng = [s_ for s_ in steps if s_["結果"] == "NG"]
                if not ng:
                    st.success("サーバーまで到達できています。API キーやエンドポイントの指定を見直してください。")
                elif ng[0]["段階"] == "DNS 解決":
                    st.error("ホスト名を解決できません。URL のつづりを確認してください。")
                elif ng[0]["段階"] in ("TCP 接続", "TLS ハンドシェイク"):
                    st.error(
                        "サーバーが接続を拒否しています。このアプリの稼働場所（クラウドの IP）からのアクセスが、"
                        "サーバー側のファイアウォールや IP 制限で遮断されている可能性が高いです。"
                        "スマートフォンのモバイル回線など、研究室外のネットワークから Makimono を開けるか確認してください。"
                        "開けない場合は学内限定の公開設定なので、PC 上で run.sh から起動するか、管理者に許可 IP の追加を依頼してください。"
                    )
                else:
                    st.warning("TLS までは成功しています。HTTP 層で拒否されている場合は、User-Agent や WAF の設定が原因かもしれません。")

        with st.expander("🔍 エンドポイント探索", expanded=False):
            st.caption("API 仕様が不明なときに、よくあるパスへ一括アクセスして応答を調べます。")
            extra_paths = st.text_area("追加で試すパス（1 行 1 つ）", "", height=60)
            if st.button("探索を実行", use_container_width=True):
                paths = COMMON_ENDPOINTS + [p.strip() for p in extra_paths.splitlines() if p.strip()]
                prog = st.progress(0.0, text="探索中…")
                rows = []
                for i, p in enumerate(paths):
                    r = client.request(p)
                    rows.append({"パス": p, "HTTP": r.status, "JSON": r.is_json, "ms": r.elapsed_ms, "Content-Type": r.content_type[:40], "プレビュー": r.preview(120)})
                    prog.progress((i + 1) / len(paths), text=f"{p}")
                prog.empty()
                st.session_state["probe_results"] = pd.DataFrame(rows)
            if st.session_state.get("probe_results") is not None:
                pr = st.session_state["probe_results"]
                st.dataframe(pr.sort_values(["JSON", "HTTP"], ascending=[False, True]), use_container_width=True, hide_index=True, height=260)

    # --------------------------------------------------------- file source
    elif source == "ファイルアップロード":
        up = st.file_uploader("CSV / TSV / JSON / Excel", type=["csv", "tsv", "txt", "json", "jsonl", "ndjson", "xlsx", "xlsm", "xls"])
        if up is not None and st.button("読み込む", type="primary", use_container_width=True):
            try:
                df = load_file(up.name, up.getvalue())
                set_dataframe(df, f"ファイル {up.name}")
                st.success(f"{len(df)} 行 × {df.shape[1]} 列")
            except Exception as exc:  # noqa: BLE001
                st.error(f"読み込みに失敗: {exc}")
        pasted = st.text_area("または JSON を貼り付け", "", height=100)
        if pasted.strip() and st.button("JSON を読み込む", use_container_width=True):
            try:
                obj = json.loads(pasted)
                recs, used = extract_records(obj)
                set_dataframe(records_to_df(recs), f"貼り付け JSON [{used}]")
                st.success(f"{len(recs)} 件")
            except json.JSONDecodeError as exc:
                st.error(f"JSON が不正です: {exc}")

    # ------------------------------------------------------- sample source
    else:
        st.caption("動作確認用の合成データ（実験条件・測定値・担当者など）")
        if st.button("サンプルを読み込む", type="primary", use_container_width=True):
            set_dataframe(pd.read_csv(SAMPLE_PATH), "サンプルデータ")

    st.divider()
    if st.session_state.get("raw_df") is not None:
        st.caption(f"現在のデータ: **{st.session_state['source_label']}**")
        if st.button("データをクリア", use_container_width=True):
            st.session_state["raw_df"] = None
            st.session_state["raw_json"] = None
            st.rerun()


# ====================================================================== main
df_all: pd.DataFrame | None = st.session_state.get("raw_df")

if df_all is None or df_all.empty:
    st.title("Makimono データ可視化・分析")
    st.info("左のサイドバーからデータを取得・読み込んでください。初めての場合は「サンプルデータ」で操作を確認できます。")
    with st.expander("使い方", expanded=True):
        st.markdown(
            """
1. **Makimono API** — ベース URL と API キーを設定し、エンドポイントを指定して「取得」。
   仕様が不明な場合は「エンドポイント探索」で JSON を返すパスを探せます。
2. **レコード配列の選択** — 取得した JSON の中から表にしたい配列を選び「表に展開」。
3. 上部のタブで **概要 → データ（絞り込み） → 可視化 → 統計分析 → エクスポート** の順に進みます。
   データタブで絞り込んだ内容は、すべてのタブに反映されます。
            """
        )
    st.stop()

roles = column_roles(df_all)
roles["numeric"] = rank_numeric(df_all, roles["numeric"])
tab_overview, tab_data, tab_viz, tab_stats, tab_export = st.tabs(["📋 概要", "🔎 データ・絞り込み", "📈 可視化", "📊 統計分析", "⬇️ エクスポート"])


# ------------------------------------------------------------ data & filters
with tab_data:
    st.subheader("絞り込み")
    st.caption("ここで絞り込んだデータが、他のタブすべてで使われます。")
    filt = df_all.copy()
    fc1, fc2 = st.columns([1, 1])

    with fc1:
        search = st.text_input("全文検索（全列を対象）", "", placeholder="キーワード")
        if search:
            mask = filt.astype(str).apply(lambda col: col.str.contains(search, case=False, na=False, regex=False)).any(axis=1)
            filt = filt[mask]
        date_cols = roles["datetime"]
        if date_cols:
            dcol = st.selectbox("期間で絞る列", ["（なし）"] + date_cols)
            if dcol != "（なし）" and filt[dcol].notna().any():
                dmin, dmax = filt[dcol].min().date(), filt[dcol].max().date()
                rng = st.date_input("期間", (dmin, dmax), min_value=dmin, max_value=dmax)
                if isinstance(rng, tuple) and len(rng) == 2:
                    s, e = rng
                    filt = filt[(filt[dcol] >= pd.Timestamp(s)) & (filt[dcol] < pd.Timestamp(e) + pd.Timedelta(days=1))]

    with fc2:
        cat_pick = st.multiselect("カテゴリで絞る列", roles["categorical"] + roles["bool"], max_selections=6)
        for col in cat_pick:
            opts = sorted(filt[col].dropna().astype(str).unique().tolist())
            sel = st.multiselect(f"{col}", opts, default=[], placeholder="すべて")
            if sel:
                filt = filt[filt[col].astype(str).isin(sel)]
        num_pick = st.multiselect("数値範囲で絞る列", roles["numeric"], max_selections=6)
        for col in num_pick:
            s = filt[col].dropna()
            if s.empty:
                continue
            lo, hi = float(s.min()), float(s.max())
            if lo == hi:
                continue
            r = st.slider(f"{col}", lo, hi, (lo, hi))
            filt = filt[(filt[col].isna()) | ((filt[col] >= r[0]) & (filt[col] <= r[1]))]

    st.markdown(f"**{len(filt):,} / {len(df_all):,} 行** を選択中")
    show_cols = st.multiselect("表示する列", list(filt.columns), default=list(filt.columns)[:30])
    st.dataframe(filt[show_cols] if show_cols else filt, use_container_width=True, height=480)

df = filt


# ---------------------------------------------------------------- overview
with tab_overview:
    st.subheader("データ概要")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("行数", f"{len(df):,}")
    m2.metric("列数", f"{df.shape[1]:,}")
    m3.metric("数値列", len(roles["numeric"]))
    m4.metric("日時列", len(roles["datetime"]))
    m5.metric("欠損セル率", f"{df.isna().mean().mean() * 100:.1f}%")
    if roles["datetime"]:
        dcol = roles["datetime"][0]
        if df[dcol].notna().any():
            st.caption(f"期間（{dcol}）: {df[dcol].min():%Y-%m-%d} 〜 {df[dcol].max():%Y-%m-%d}")

    st.markdown("##### 列のプロファイル")
    st.dataframe(summarize(df), use_container_width=True, hide_index=True, height=min(600, 40 + 35 * len(df.columns)))

    if roles["datetime"] and roles["numeric"]:
        st.markdown("##### 数値列の推移（スモールマルチプル）")
        c1, c2 = st.columns([1, 3])
        with c1:
            xcol = st.selectbox("横軸（日時）", roles["datetime"], key="ov_x")
            ycols = st.multiselect("表示する数値列", roles["numeric"], default=roles["numeric"][:6], key="ov_y")
            ccol = st.selectbox("色分け", ["（なし）"] + roles["categorical"], key="ov_c")
        with c2:
            if ycols:
                st.plotly_chart(charts.small_multiples(df, xcol, ycols, None if ccol == "（なし）" else ccol), use_container_width=True)

    miss = df.isna().mean().sort_values(ascending=False)
    miss = miss[miss > 0]
    if not miss.empty:
        st.markdown("##### 欠損率の高い列")
        st.bar_chart((miss * 100).round(1).head(20), horizontal=True, color="#86b6ef")


# ----------------------------------------------------------------- charts
with tab_viz:
    st.subheader("チャートビルダー")
    all_cols = list(df.columns)
    c_ctl, c_plot = st.columns([1, 3])
    with c_ctl:
        kind_label = st.selectbox("グラフの種類", list(charts.CHART_KINDS.keys()))
        kind = charts.CHART_KINDS[kind_label]
        x_default = roles["datetime"] or roles["categorical"] or roles["numeric"]
        x = st.selectbox("X 軸", ["（なし）"] + all_cols, index=(all_cols.index(x_default[0]) + 1) if x_default else 0)
        x = None if x == "（なし）" else x
        if kind == "histogram":
            y = st.multiselect("値（Y）", roles["numeric"], default=[] if x else roles["numeric"][:1], max_selections=1)
        elif kind in ("box", "violin", "density_heatmap"):
            y = st.multiselect("値（Y）", roles["numeric"], default=roles["numeric"][:1], max_selections=1)
        else:
            y = st.multiselect("値（Y）※複数可", roles["numeric"], default=roles["numeric"][:1])
        color = st.selectbox("色分け", ["（なし）"] + roles["categorical"] + roles["bool"], key="viz_color")
        color = None if color == "（なし）" else color
        facet = st.selectbox("分割（ファセット）", ["（なし）"] + roles["categorical"], key="viz_facet")
        facet = None if facet == "（なし）" else facet
        agg_label = st.selectbox("集計", list(charts.AGG_FUNCS.keys()), index=0, disabled=kind not in ("line", "bar", "area", "scatter"))
        agg = charts.AGG_FUNCS[agg_label]
        log_y = st.checkbox("Y 軸を対数")
        trend = st.checkbox("回帰直線（散布図）", disabled=kind != "scatter")
        title = st.text_input("タイトル", "")

    with c_plot:
        if kind == "histogram" and not x and not y:
            st.info("X 軸または値を選んでください。")
        elif kind != "histogram" and not y:
            st.info("値（Y）を 1 つ以上選んでください。")
        else:
            try:
                fig = charts.build_chart(df, kind, x, y, color, facet, agg, log_y, trend, title or kind_label)
                st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False, "toImageButtonOptions": {"format": "png", "scale": 2}})
                b1, b2 = st.columns([1, 4])
                if b1.button("📌 保存"):
                    st.session_state["saved_charts"].append({"title": title or kind_label, "fig": fig.to_json()})
                b2.download_button("HTML でダウンロード", fig.to_html(include_plotlyjs="cdn"), file_name="chart.html", mime="text/html")
            except Exception as exc:  # noqa: BLE001
                st.error(f"描画に失敗しました: {exc}")

    if st.session_state["saved_charts"]:
        st.divider()
        st.markdown("##### 保存したチャート")
        cols = st.columns(2)
        import plotly.io as pio

        for i, item in enumerate(st.session_state["saved_charts"]):
            with cols[i % 2]:
                st.plotly_chart(pio.from_json(item["fig"]), use_container_width=True, key=f"saved_{i}")
                if st.button("削除", key=f"del_{i}"):
                    st.session_state["saved_charts"].pop(i)
                    st.rerun()


# ------------------------------------------------------------------ stats
with tab_stats:
    st.subheader("統計分析")
    s_tabs = st.tabs(["基本統計量", "グループ集計", "相関", "回帰・トレンド", "群間比較", "外れ値"])
    num_cols = roles["numeric"]

    with s_tabs[0]:
        if num_cols:
            desc = df[num_cols].describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95]).T
            desc["CV(%)"] = (desc["std"] / desc["mean"].replace(0, np.nan) * 100).round(2)
            st.dataframe(desc.round(4), use_container_width=True)
        else:
            st.info("数値列がありません。")

    with s_tabs[1]:
        g1, g2, g3 = st.columns(3)
        gcols = g1.multiselect("グループ化する列", roles["categorical"] + roles["bool"] + roles["datetime"], key="g_cols")
        vcols = g2.multiselect("集計する列", num_cols, default=num_cols[:3], key="g_vals")
        funcs = g3.multiselect("集計関数", ["count", "mean", "median", "std", "min", "max", "sum"], default=["count", "mean", "std"], key="g_funcs")
        freq = None
        if any(c in roles["datetime"] for c in gcols):
            freq = st.selectbox("日時列の粒度", ["日", "週", "月", "四半期", "年"], index=2)
        if gcols and vcols and funcs:
            gdf = df.copy()
            keys = []
            for c in gcols:
                if c in roles["datetime"]:
                    rule = {"日": "D", "週": "W", "月": "MS", "四半期": "QS", "年": "YS"}[freq]
                    keys.append(pd.Grouper(key=c, freq=rule))
                else:
                    keys.append(c)
            res = gdf.groupby(keys, dropna=False, observed=True)[vcols].agg(funcs)
            res.columns = [f"{a}_{b}" for a, b in res.columns]
            res = res.reset_index()
            st.dataframe(res, use_container_width=True, height=400)
            st.download_button("集計結果を CSV で保存", res.to_csv(index=False).encode("utf-8-sig"), "groupby.csv", "text/csv")
            if len(gcols) == 1 and f"{vcols[0]}_mean" in res.columns:
                st.plotly_chart(
                    charts.build_chart(res, "bar", gcols[0], f"{vcols[0]}_mean", title=f"{vcols[0]} の平均（{gcols[0]} 別）"),
                    use_container_width=True,
                )
        else:
            st.info("グループ化する列・集計する列・関数を選んでください。")

    with s_tabs[2]:
        if len(num_cols) >= 2:
            method = st.radio("相関係数", ["pearson", "spearman"], horizontal=True)
            sel = st.multiselect("対象列", num_cols, default=num_cols[:12], key="corr_cols")
            if len(sel) >= 2:
                corr = df[sel].corr(method=method)
                st.plotly_chart(charts.correlation_heatmap(corr, f"相関行列（{method}）"), use_container_width=True)
                pairs = corr.where(np.triu(np.ones(corr.shape), 1).astype(bool)).stack().reset_index()
                pairs.columns = ["変数1", "変数2", "r"]
                pairs["|r|"] = pairs["r"].abs()
                st.markdown("##### 相関が強いペア")
                st.dataframe(pairs.sort_values("|r|", ascending=False).head(15).round(3), hide_index=True, use_container_width=True)
        else:
            st.info("数値列が 2 つ以上必要です。")

    with s_tabs[3]:
        if len(num_cols) >= 1 and (num_cols or roles["datetime"]):
            r1, r2 = st.columns(2)
            xr = r1.selectbox("説明変数 X", roles["datetime"] + num_cols, key="reg_x")
            yr = r2.selectbox("目的変数 Y", [c for c in num_cols if c != xr] or num_cols, key="reg_y")
            sub = df[[xr, yr]].dropna()
            if len(sub) >= 3 and xr != yr:
                xv = sub[xr]
                if pd.api.types.is_datetime64_any_dtype(xv):
                    xnum = (xv - xv.min()).dt.total_seconds() / 86400.0
                    xlabel = f"{xr} からの経過日数"
                else:
                    xnum = xv.astype(float)
                    xlabel = xr
                try:
                    import statsmodels.api as sm

                    X = sm.add_constant(xnum.values)
                    model = sm.OLS(sub[yr].values, X).fit()
                    slope, intercept = model.params[1], model.params[0]
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("傾き（/単位X）", f"{slope:.4g}")
                    k2.metric("切片", f"{intercept:.4g}")
                    k3.metric("R²", f"{model.rsquared:.3f}")
                    k4.metric("p 値（傾き）", f"{model.pvalues[1]:.3g}")
                    st.caption(f"X: {xlabel} / n = {len(sub)}")
                    plot_df = pd.DataFrame({xr: xv, yr: sub[yr], "回帰": model.fittedvalues})
                    import plotly.graph_objects as go

                    fig = go.Figure()
                    fig.add_scatter(x=plot_df[xr], y=plot_df[yr], mode="markers", name=yr, marker=dict(size=8, color=charts.CATEGORICAL[0], line=dict(width=1, color=charts.SURFACE)))
                    order = plot_df.sort_values(xr)
                    fig.add_scatter(x=order[xr], y=order["回帰"], mode="lines", name="回帰直線", line=dict(width=2, color=charts.CATEGORICAL[1]))
                    fig.update_layout(title=f"{yr} vs {xr}", xaxis_title=xr, yaxis_title=yr, height=480)
                    st.plotly_chart(fig, use_container_width=True)
                    with st.expander("statsmodels サマリー"):
                        st.code(str(model.summary()))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"回帰に失敗: {exc}")
            else:
                st.info("有効なデータが 3 行以上必要です。")
        else:
            st.info("数値列がありません。")

    with s_tabs[4]:
        if num_cols and (roles["categorical"] or roles["bool"]):
            t1, t2 = st.columns(2)
            gcol = t1.selectbox("群を表す列", roles["categorical"] + roles["bool"], key="tt_g")
            vcol = t2.selectbox("比較する値", num_cols, key="tt_v")
            groups = {k: v[vcol].dropna().values for k, v in df.groupby(gcol, observed=True) if v[vcol].notna().sum() >= 2}
            if len(groups) >= 2:
                from scipy import stats

                summary = pd.DataFrame({"群": list(groups.keys()), "n": [len(v) for v in groups.values()], "平均": [float(np.mean(v)) for v in groups.values()], "標準偏差": [float(np.std(v, ddof=1)) for v in groups.values()], "中央値": [float(np.median(v)) for v in groups.values()]})
                st.dataframe(summary.round(4), hide_index=True, use_container_width=True)
                vals = list(groups.values())
                if len(vals) == 2:
                    t, p = stats.ttest_ind(vals[0], vals[1], equal_var=False)
                    u, pu = stats.mannwhitneyu(vals[0], vals[1])
                    c1, c2 = st.columns(2)
                    c1.metric("Welch の t 検定 p 値", f"{p:.3g}", help=f"t = {t:.3f}")
                    c2.metric("Mann-Whitney U 検定 p 値", f"{pu:.3g}", help=f"U = {u:.1f}")
                else:
                    f, p = stats.f_oneway(*vals)
                    h, ph = stats.kruskal(*vals)
                    c1, c2 = st.columns(2)
                    c1.metric("一元配置分散分析 p 値", f"{p:.3g}", help=f"F = {f:.3f}")
                    c2.metric("Kruskal-Wallis p 値", f"{ph:.3g}", help=f"H = {h:.3f}")
                st.plotly_chart(charts.build_chart(df[[gcol, vcol]].dropna(), "box", gcol, vcol, color=gcol, title=f"{vcol}（{gcol} 別）"), use_container_width=True)
            else:
                st.info("2 件以上のデータを持つ群が 2 つ以上必要です。")
        else:
            st.info("数値列とカテゴリ列が必要です。")

    with s_tabs[5]:
        if num_cols:
            o1, o2, o3 = st.columns(3)
            ocol = o1.selectbox("対象列", num_cols, key="out_col")
            method_o = o2.radio("方法", ["IQR (1.5倍)", "Z スコア"], horizontal=True)
            thr = o3.number_input("Z 閾値", 1.0, 6.0, 3.0, 0.5, disabled=method_o != "Z スコア")
            s = df[ocol]
            if method_o.startswith("IQR"):
                q1, q3 = s.quantile(0.25), s.quantile(0.75)
                iqr = q3 - q1
                mask = (s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)
                st.caption(f"正常範囲: {q1 - 1.5 * iqr:.4g} 〜 {q3 + 1.5 * iqr:.4g}")
            else:
                z = (s - s.mean()) / s.std(ddof=0)
                mask = z.abs() > thr
            st.metric("外れ値の件数", int(mask.sum()))
            if mask.any():
                st.dataframe(df[mask], use_container_width=True, height=300)
            st.plotly_chart(charts.build_chart(df, "box", None, ocol, title=f"{ocol} の分布"), use_container_width=True)
        else:
            st.info("数値列がありません。")


# ----------------------------------------------------------------- export
with tab_export:
    st.subheader("エクスポート")
    st.caption(f"絞り込み後のデータ {len(df):,} 行 × {df.shape[1]} 列")
    e1, e2, e3 = st.columns(3)
    e1.download_button("CSV (UTF-8 BOM)", df.to_csv(index=False).encode("utf-8-sig"), f"makimono_{date.today():%Y%m%d}.csv", "text/csv", use_container_width=True)
    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as w:
        df_x = df.copy()
        for c in df_x.columns:
            if pd.api.types.is_datetime64_any_dtype(df_x[c]) and getattr(df_x[c].dt, "tz", None) is not None:
                df_x[c] = df_x[c].dt.tz_localize(None)
        df_x.to_excel(w, index=False, sheet_name="data")
        summarize(df).to_excel(w, index=False, sheet_name="profile")
        if num_cols:
            df[num_cols].describe().T.to_excel(w, sheet_name="describe")
    e2.download_button("Excel (.xlsx)", xbuf.getvalue(), f"makimono_{date.today():%Y%m%d}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    e3.download_button("JSON", df.to_json(orient="records", force_ascii=False, date_format="iso", indent=2), f"makimono_{date.today():%Y%m%d}.json", "application/json", use_container_width=True)
    if st.session_state.get("raw_json") is not None:
        st.download_button("API 生レスポンス (JSON)", json.dumps(st.session_state["raw_json"], ensure_ascii=False, indent=2, default=str), "raw_response.json", "application/json")
