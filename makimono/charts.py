"""Plotly チャート生成。配色・マーク仕様は dataviz ガイドラインに準拠。"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

# カテゴリ配色（固定順。9系列以上は「その他」に畳む）
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQUENTIAL = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
DIVERGING = ["#0d366b", "#2a78d6", "#86b6ef", "#f0efec", "#f5b08e", "#eb6834", "#8a2e0f"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e6e5e1"

_template = go.layout.Template()
_template.layout = go.Layout(
    colorway=CATEGORICAL,
    paper_bgcolor=SURFACE,
    plot_bgcolor=SURFACE,
    font=dict(color=INK, size=13, family="Inter, 'Noto Sans JP', 'Hiragino Sans', Meiryo, sans-serif"),
    title=dict(font=dict(size=16), x=0.0, xanchor="left", y=0.98, yanchor="top", yref="container"),
    xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, tickfont=dict(color=INK2), title_font=dict(color=INK2)),
    yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, tickfont=dict(color=INK2), title_font=dict(color=INK2)),
    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, font=dict(color=INK2), title_text=""),
    hoverlabel=dict(bgcolor="#ffffff", bordercolor=GRID, font=dict(color=INK)),
    margin=dict(l=48, r=24, t=96, b=56),
    bargap=0.25,
)
pio.templates["makimono"] = _template
pio.templates.default = "makimono"

CHART_KINDS = {
    "折れ線（時系列）": "line",
    "散布図": "scatter",
    "棒グラフ": "bar",
    "箱ひげ図": "box",
    "バイオリン": "violin",
    "ヒストグラム": "histogram",
    "面グラフ": "area",
    "ヒートマップ（2変数集計）": "density_heatmap",
}

AGG_FUNCS = {"なし（生データ）": None, "平均": "mean", "合計": "sum", "中央値": "median", "件数": "count", "最大": "max", "最小": "min", "標準偏差": "std"}


def _fold_categories(df: pd.DataFrame, col: str | None, limit: int = 8) -> pd.DataFrame:
    """9 カテゴリ以上は上位 7 + その他 に畳む（色を使い回さないため）。"""
    if not col or col not in df.columns:
        return df
    s = df[col].astype(str)
    if s.nunique() <= limit:
        return df
    top = s.value_counts().head(limit - 1).index
    df = df.copy()
    df[col] = s.where(s.isin(top), "その他")
    return df


def _style(fig: go.Figure, kind: str) -> go.Figure:
    if kind in ("line", "area"):
        fig.update_traces(line=dict(width=2), marker=dict(size=6))
    elif kind == "scatter":
        fig.update_traces(marker=dict(size=8, line=dict(width=1, color=SURFACE), opacity=0.85))
    elif kind in ("bar", "histogram"):
        fig.update_traces(marker=dict(line=dict(width=1, color=SURFACE)))
    fig.update_layout(hovermode="x unified" if kind in ("line", "area") else "closest")
    return fig


def build_chart(
    df: pd.DataFrame,
    kind: str,
    x: str | None,
    y: list[str] | str | None,
    color: str | None = None,
    facet: str | None = None,
    agg: str | None = None,
    log_y: bool = False,
    trendline: bool = False,
    title: str = "",
) -> go.Figure:
    ys = [y] if isinstance(y, str) else list(y or [])
    plot_df = _fold_categories(df, color)
    if facet:
        plot_df = _fold_categories(plot_df, facet, limit=12)

    # 複数 y 列 → long 形式に変換して色で区別
    if len(ys) > 1:
        id_vars = [c for c in [x, color, facet] if c]
        plot_df = plot_df.melt(id_vars=id_vars, value_vars=ys, var_name="系列", value_name="値")
        y_col, color = "値", (color or "系列")
    else:
        y_col = ys[0] if ys else None

    # 集計
    if agg and x and y_col and kind in ("line", "bar", "area", "scatter"):
        keys = [c for c in [x, color, facet] if c]
        plot_df = plot_df.groupby(keys, dropna=False, observed=True)[y_col].agg(agg).reset_index()

    common = dict(x=x, color=color, facet_col=facet, facet_col_wrap=3 if facet else None, title=title)
    if kind == "line":
        fig = px.line(plot_df.sort_values(x) if x else plot_df, y=y_col, markers=len(plot_df) < 300, **common)
    elif kind == "area":
        fig = px.area(plot_df.sort_values(x) if x else plot_df, y=y_col, **common)
    elif kind == "scatter":
        fig = px.scatter(plot_df, y=y_col, trendline="ols" if trendline else None, **common)
    elif kind == "bar":
        fig = px.bar(plot_df, y=y_col, barmode="group", **common)
    elif kind == "box":
        fig = px.box(plot_df, y=y_col, points="outliers", **common)
    elif kind == "violin":
        fig = px.violin(plot_df, y=y_col, box=True, points="outliers", **common)
    elif kind == "histogram":
        fig = px.histogram(plot_df, y=None if x else y_col, x=x or y_col, barmode="overlay", opacity=0.8, **{k: v for k, v in common.items() if k != "x"})
    elif kind == "density_heatmap":
        fig = px.density_heatmap(plot_df, x=x, y=y_col, color_continuous_scale=SEQUENTIAL, title=title, facet_col=facet)
    else:
        raise ValueError(kind)

    if log_y:
        fig.update_yaxes(type="log")
    fig.update_layout(height=520)
    return _style(fig, kind)


def correlation_heatmap(corr: pd.DataFrame, title: str = "相関行列（Pearson）") -> go.Figure:
    fig = go.Figure(
        go.Heatmap(
            z=corr.values,
            x=corr.columns,
            y=corr.index,
            zmin=-1,
            zmax=1,
            colorscale=DIVERGING,
            text=corr.round(2).values,
            texttemplate="%{text}",
            textfont=dict(size=11),
            hovertemplate="%{y} × %{x}<br>r = %{z:.3f}<extra></extra>",
            xgap=2,
            ygap=2,
        )
    )
    fig.update_layout(title=title, height=max(400, 32 * len(corr) + 120), yaxis=dict(autorange="reversed"))
    return fig


def small_multiples(df: pd.DataFrame, x: str, cols: list[str], color: str | None = None) -> go.Figure:
    long = df.melt(id_vars=[c for c in [x, color] if c], value_vars=cols, var_name="指標", value_name="値")
    long = _fold_categories(long, color)
    fig = px.line(
        long.sort_values(x),
        x=x,
        y="値",
        color=color,
        facet_col="指標",
        facet_col_wrap=3,
        markers=len(df) < 300,
        facet_row_spacing=0.08,
    )
    fig.update_yaxes(matches=None, showticklabels=True)
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    fig.update_layout(height=max(360, 280 * ((len(cols) + 2) // 3)))
    return _style(fig, "line")
