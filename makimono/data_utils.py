"""JSON / ファイルから DataFrame を組み立て、型を整えるユーティリティ。"""
from __future__ import annotations

import io
import json
import re
from typing import Any

import pandas as pd

# "12.3 mg", "1,234", "45%", "-0.5e3 µL" などから数値と単位を分離
_NUM_UNIT_RE = re.compile(
    r"^\s*([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s\-/:.,][^\s]{0,15})?\s*$"
)
_DATE_HINT_RE = re.compile(r"(date|time|day|at$|_on$|created|updated|modified|timestamp|日時|日付|時刻)", re.I)


# ---------------------------------------------------------------- JSON 探索
def get_by_path(obj: Any, path: str | None) -> Any:
    """'data.items[0].rows' のようなパスで JSON を辿る。空パスなら obj 自身。"""
    if not path or path in ("$", "."):
        return obj
    path = re.sub(r"^\$\.?", "", path.strip())
    if not path:
        return obj
    cur = obj
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if token.startswith("["):
            idx = int(token[1:-1])
            if isinstance(cur, list) and -len(cur) <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            if isinstance(cur, dict) and token in cur:
                cur = cur[token]
            else:
                return None
    return cur


def find_record_arrays(obj: Any, path: str = "$", max_depth: int = 6) -> list[tuple[str, int, list[str]]]:
    """JSON 内の「辞書のリスト」候補を (パス, 件数, 主なキー) で列挙する。"""
    found: list[tuple[str, int, list[str]]] = []

    def walk(o: Any, p: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(o, list):
            dicts = [x for x in o if isinstance(x, dict)]
            if dicts and len(dicts) >= max(1, len(o) // 2):
                keys: list[str] = []
                for d in dicts[:20]:
                    for k in d.keys():
                        if k not in keys:
                            keys.append(k)
                found.append((p, len(dicts), keys[:12]))
            for i, x in enumerate(o[:5]):
                if isinstance(x, (dict, list)):
                    walk(x, f"{p}[{i}]", depth + 1)
        elif isinstance(o, dict):
            # {id: {...}, id2: {...}} 形式（キーが ID の辞書）も候補に含める
            vals = list(o.values())
            if len(vals) >= 2 and all(isinstance(v, dict) for v in vals):
                keys = sorted({k for v in vals[:20] for k in v.keys()})
                found.append((p + " (dict-of-dicts)", len(vals), keys[:12]))
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v, f"{p}.{k}", depth + 1)

    walk(obj, path, 0)
    found.sort(key=lambda t: -t[1])
    return found


def extract_records(obj: Any, path: str | None = None) -> tuple[list[dict], str]:
    """最も「表」らしいレコード配列を取り出す。戻り値 (records, 使ったパス)。"""
    if path:
        target = get_by_path(obj, path.replace(" (dict-of-dicts)", ""))
        if isinstance(target, dict) and all(isinstance(v, dict) for v in target.values()):
            return [{"_key": k, **v} for k, v in target.items()], path
        if isinstance(target, list):
            return [x for x in target if isinstance(x, dict)], path
        if isinstance(target, dict):
            return [target], path
        return [], path

    if isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        if dicts:
            return dicts, "$"
        # スカラーのリスト
        return [{"value": x} for x in obj], "$"

    candidates = find_record_arrays(obj)
    if candidates:
        best_path = candidates[0][0]
        return extract_records(obj, best_path)
    if isinstance(obj, dict):
        return [obj], "$"
    return [], "$"


# ------------------------------------------------------------- DataFrame 化
def records_to_df(records: list[dict], sep: str = ".") -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.json_normalize(records, sep=sep, max_level=3)
    # リスト/辞書のセルは JSON 文字列に
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
    return df


def split_number_unit(series: pd.Series) -> tuple[pd.Series, pd.Series, float]:
    """文字列列から数値部と単位部を分離。戻り値 (数値, 単位, 変換成功率)。"""
    s = series.astype("string")
    m = s.str.extract(_NUM_UNIT_RE)
    nums = pd.to_numeric(m[0].str.replace(",", "", regex=False), errors="coerce")
    units = m[1].fillna("").str.strip()
    nonnull = s.notna() & (s.str.strip() != "")
    ratio = float(nums.notna().sum() / max(1, nonnull.sum()))
    return nums, units, ratio


def coerce_types(df: pd.DataFrame, numeric_threshold: float = 0.8, date_threshold: float = 0.8) -> pd.DataFrame:
    """object 列を数値 / 日時に自動変換する。単位付き数値は <col> + <col>_unit に分割。"""
    out = df.copy()
    for col in list(out.columns):
        s = out[col]
        if s.dtype != object and not isinstance(s.dtype, pd.StringDtype):
            continue
        nonnull = s.dropna()
        nonnull = nonnull[nonnull.astype(str).str.strip() != ""]
        if nonnull.empty:
            continue

        # 真偽値
        lowered = nonnull.astype(str).str.lower().unique()
        if set(lowered) <= {"true", "false", "yes", "no", "はい", "いいえ"} and len(lowered) <= 2:
            out[col] = s.map(lambda v: None if pd.isna(v) else str(v).lower() in {"true", "yes", "はい"})
            continue

        # 日時（列名ヒント、または日付らしい文字列が過半数）
        looks_date = bool(_DATE_HINT_RE.search(str(col)))
        sample = nonnull.astype(str).head(200)
        if looks_date or sample.str.contains(r"\d{4}[-/年]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}:\d{2}", regex=True).mean() > 0.5:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            if parsed.notna().mean() >= date_threshold:
                full = pd.to_datetime(s.astype(str).where(s.notna()), errors="coerce", format="mixed", utc=True)
                out[col] = full.dt.tz_convert(None)
                continue

        # 数値（単位付き含む）
        nums, units, ratio = split_number_unit(s)
        if ratio >= numeric_threshold:
            unit_vals = units[units != ""].unique()
            out[col] = nums
            if 0 < len(unit_vals) <= 5:
                out[f"{col}_unit"] = units.where(units != "", None)
            continue
    return out


def column_roles(df: pd.DataFrame, max_categories: int = 60) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {"numeric": [], "datetime": [], "categorical": [], "text": [], "bool": []}
    n = max(1, len(df))
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_bool_dtype(s):
            roles["bool"].append(col)
        elif pd.api.types.is_numeric_dtype(s):
            roles["numeric"].append(col)
        elif pd.api.types.is_datetime64_any_dtype(s):
            roles["datetime"].append(col)
        else:
            nun = s.nunique(dropna=True)
            if nun <= max_categories or nun / n < 0.3:
                roles["categorical"].append(col)
            else:
                roles["text"].append(col)
    return roles


def rank_numeric(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """ユニーク数が多い（連続量らしい）数値列を先頭にした順序を返す。"""
    return sorted(cols, key=lambda c: -int(df[c].nunique(dropna=True)))


# ----------------------------------------------------------- ファイル読込
def load_file(name: str, data: bytes) -> pd.DataFrame:
    lower = name.lower()
    bio = io.BytesIO(data)
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(bio)
    if lower.endswith(".tsv") or lower.endswith(".txt"):
        return pd.read_csv(bio, sep="\t")
    if lower.endswith(".csv"):
        for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
            try:
                bio.seek(0)
                return pd.read_csv(bio, encoding=enc)
            except UnicodeDecodeError:
                continue
        bio.seek(0)
        return pd.read_csv(bio, encoding_errors="replace")
    if lower.endswith((".json", ".ndjson", ".jsonl")):
        text = data.decode("utf-8-sig", errors="replace")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = [json.loads(line) for line in text.splitlines() if line.strip()]
        recs, _ = extract_records(obj)
        return records_to_df(recs)
    # 不明な拡張子は CSV として試す
    bio.seek(0)
    return pd.read_csv(bio, sep=None, engine="python")


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        s = df[col]
        info = {
            "列名": col,
            "型": str(s.dtype),
            "非欠損": int(s.notna().sum()),
            "欠損率": round(float(s.isna().mean()) * 100, 1),
            "ユニーク数": int(s.nunique(dropna=True)),
        }
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            info["最小"] = f"{s.min():.6g}" if s.notna().any() else ""
            info["最大"] = f"{s.max():.6g}" if s.notna().any() else ""
            info["平均"] = f"{s.mean():.6g}" if s.notna().any() else ""
        elif pd.api.types.is_datetime64_any_dtype(s):
            info["最小"] = str(s.min())[:19] if s.notna().any() else ""
            info["最大"] = str(s.max())[:19] if s.notna().any() else ""
        else:
            top = s.dropna().astype(str).value_counts().head(3)
            info["例"] = ", ".join(top.index.tolist())
        rows.append(info)
    out = pd.DataFrame(rows)
    for c in ("最小", "最大", "平均", "例"):
        if c in out.columns:
            out[c] = out[c].fillna("").astype(str)
    return out
