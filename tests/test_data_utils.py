import json

import pandas as pd

from makimono.data_utils import coerce_types, column_roles, extract_records, find_record_arrays, get_by_path, load_file, records_to_df


def test_extract_from_wrapped_list():
    obj = {"status": "ok", "data": {"items": [{"id": 1, "v": "2 mg"}, {"id": 2, "v": "3 mg"}], "total": 2}}
    recs, path = extract_records(obj)
    assert path == "$.data.items"
    assert len(recs) == 2


def test_extract_dict_of_dicts():
    obj = {"results": {"a": {"x": 1}, "b": {"x": 2}, "c": {"x": 3}}}
    cands = find_record_arrays(obj)
    assert cands[0][0].startswith("$.results")
    recs, _ = extract_records(obj, cands[0][0])
    assert {r["_key"] for r in recs} == {"a", "b", "c"}


def test_get_by_path():
    obj = {"a": [{"b": {"c": 5}}]}
    assert get_by_path(obj, "$.a[0].b.c") == 5
    assert get_by_path(obj, "a[0].b") == {"c": 5}
    assert get_by_path(obj, "a[3]") is None


def test_coerce_units_and_dates():
    df = pd.DataFrame(
        {
            "temp": ["25 ℃", "40 ℃", "60 ℃", None],
            "amount": ["1,234 mg", "12.5 mg", "0.5 mg", "7 mg"],
            "created_at": ["2025-01-01 10:00", "2025-01-02 11:30", "2025/01/03 09:00", None],
            "name": ["a", "b", "c", "d"],
            "flag": ["true", "false", "true", None],
            "pct": ["10%", "20%", "30%", "40%"],
        }
    )
    out = coerce_types(df)
    assert pd.api.types.is_numeric_dtype(out["temp"]) and out["temp_unit"].dropna().unique().tolist() == ["℃"]
    assert out["amount"].iloc[0] == 1234.0
    assert pd.api.types.is_datetime64_any_dtype(out["created_at"])
    assert not pd.api.types.is_numeric_dtype(out["name"])
    assert out["flag"].iloc[1] is False
    assert out["pct"].iloc[2] == 30.0
    roles = column_roles(out)
    assert "temp" in roles["numeric"] and "created_at" in roles["datetime"] and "name" in roles["categorical"]


def test_records_to_df_nested():
    recs = [{"id": 1, "meta": {"user": "x", "tags": ["a", "b"]}}, {"id": 2, "meta": {"user": "y", "tags": []}}]
    df = records_to_df(recs)
    assert "meta.user" in df.columns
    assert json.loads(df["meta.tags"].iloc[0]) == ["a", "b"]


def test_load_json_and_csv_bytes():
    data = json.dumps({"rows": [{"a": 1}, {"a": 2}]}).encode()
    assert len(load_file("x.json", data)) == 2
    csv = "a,b\n1,x\n2,y\n".encode("utf-8-sig")
    assert list(load_file("x.csv", csv).columns) == ["a", "b"]
    ndjson = b'{"a":1}\n{"a":2}\n'
    assert len(load_file("x.ndjson", ndjson)) == 2


def test_sample_csv_types():
    df = coerce_types(pd.read_csv("sample_data/sample_experiments.csv"))
    roles = column_roles(df)
    assert "記録日時" in roles["datetime"]
    for c in ["反応温度", "反応時間", "基質濃度", "収率", "純度", "pH", "生成物質量"]:
        assert c in roles["numeric"], c
    assert "反応温度_unit" in df.columns
