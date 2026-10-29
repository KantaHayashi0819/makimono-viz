"""動作確認用の合成データを生成する（Makimono のレコードを模した実験ログ）。"""
import numpy as np
import pandas as pd

rng = np.random.default_rng(42)
n = 360
start = pd.Timestamp("2025-11-01")
dates = start + pd.to_timedelta(np.sort(rng.integers(0, 360, n)), unit="D") + pd.to_timedelta(rng.integers(8, 20, n), unit="h")
projects = rng.choice(["触媒A開発", "触媒B開発", "溶媒スクリーニング", "耐久試験"], n, p=[0.35, 0.3, 0.2, 0.15])
operators = rng.choice(["山田", "佐藤", "鈴木", "高橋", "田中"], n)
catalyst = rng.choice(["Pd/C", "Pt/C", "Ru-1", "Ni-2", "なし"], n, p=[0.3, 0.25, 0.2, 0.15, 0.1])
temp = rng.choice([25, 40, 60, 80, 100], n)
time_h = rng.choice([1, 2, 4, 8, 24], n)
conc = np.round(rng.uniform(0.05, 0.5, n), 3)
base_yield = {"Pd/C": 78, "Pt/C": 70, "Ru-1": 62, "Ni-2": 55, "なし": 20}
yield_ = np.array([base_yield[c] for c in catalyst]) + 0.12 * (temp - 60) + 1.5 * np.log(time_h) + 20 * conc + rng.normal(0, 5, n)
yield_ = np.clip(yield_, 0, 100).round(1)
purity = np.clip(99 - 0.08 * (temp - 25) + rng.normal(0, 0.8, n), 85, 100).round(2)
ph = np.round(rng.normal(7.2, 0.6, n), 2)
mass = np.round(yield_ / 100 * 2.5 * rng.uniform(0.95, 1.05, n), 3)
status = rng.choice(["完了", "完了", "完了", "再実験", "中止"], n)
df = pd.DataFrame(
    {
        "id": [f"EXP-{i+1:04d}" for i in range(n)],
        "記録日時": dates.strftime("%Y-%m-%d %H:%M"),
        "プロジェクト": projects,
        "担当者": operators,
        "触媒": catalyst,
        "反応温度": [f"{t} ℃" for t in temp],
        "反応時間": [f"{t} h" for t in time_h],
        "基質濃度": [f"{c} M" for c in conc],
        "収率": yield_,
        "純度": [f"{p} %" for p in purity],
        "pH": ph,
        "生成物質量": [f"{m} g" for m in mass],
        "ステータス": status,
        "メモ": rng.choice(["", "", "", "撹拌速度を上げた", "着色あり", "沈殿を確認", "試薬ロット変更"], n),
    }
)
df.to_csv("sample_experiments.csv", index=False, encoding="utf-8-sig")
print(df.head(), len(df))
