#!/usr/bin/env python
"""补充审计实验 I、K、L、M（修正采样率问题）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent / "prototype"
sys.path.insert(0, str(ROOT))

from src import can_io, energy, features, modes, preprocess, simulator  # noqa: E402
from src import physics as ph  # noqa: E402
from src.physics import VehicleParams  # noqa: E402
from run_demo import _frame_from_truth  # noqa: E402

CFG = ROOT / "config"
OUT = ROOT / "outputs"


def hr(t):
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}", flush=True)


def to_grid(arr, trip, frame):
    idx = np.clip((frame.t / simulator.DT).astype(int), 0, trip.v.size - 1)
    return np.asarray(arr, dtype=float)[idx]


# ======================================================================
def exp_I(params):
    hr("实验 I：误差来源拆解 —— 「与真值误差 0.01%~0.05%」证明了什么")
    db = can_io.load_dbc(CFG / "trucks_demo.dbc")
    specs = {s.trip_id: s for s in simulator.build_fleet(40, 2, "fuel")}
    for trip_id in ("T000-2", "T005-1", "T000-1"):
        tr = simulator.simulate_trip(specs[trip_id], params)
        log = OUT / f"canlog_{trip_id}.log"

        decoded, dstat = can_io.decode_log(db, log)
        f_can = preprocess.build_frame(decoded, dstat)
        r_can = energy.analyze_frame(f_can, params, "fuel")

        f_tru = _frame_from_truth(tr, params)
        r_tru = energy.analyze_frame(f_tru, params, "fuel")
        r_ide = energy.analyze_frame(
            f_tru, params, "fuel",
            mass_override=to_grid(tr.mass, tr, f_tru),
            grade_override=to_grid(tr.grade, tr, f_tru))

        t_fuel = tr.truth["fuel_l"]
        print(f"\n  [{trip_id}] 真值油耗 {t_fuel:.4f} L ｜ 真值里程 "
              f"{tr.truth['distance_km']:.4f} km ｜ 真值牵引 "
              f"{tr.truth['e_traction_wheel_kwh']:.3f} kWh")
        print(f"    {'路径':<40}{'油耗 L':>10}{'里程 km':>10}{'对真值误差':>12}")
        for tag, r in (("① 完整报文链路（量化/噪声/丢帧）", r_can),
                       ("② 真值直通（无编解码，估计坡度/质量）", r_tru),
                       ("③ 真值直通 + 真值坡度/质量", r_ide)):
            err = abs(r.fuel_l - t_fuel) / t_fuel * 100.0
            print(f"    {tag:<40}{r.fuel_l:>10.4f}{r.distance_km:>10.4f}{err:>11.4f}%")
        print(f"    {'':<40}{'':>10}{'':>10}")
        print(f"    → ①−② 之差（编解码+噪声贡献）   : "
              f"{abs(r_can.fuel_l - r_tru.fuel_l) / t_fuel * 100:.4f} pp")
        print(f"    → ②−③ 之差（坡度/质量估计贡献） : "
              f"{abs(r_tru.fuel_l - r_ide.fuel_l) / t_fuel * 100:.4f} pp")
        print(f"    → ③ 与真值之差（纯物理模型口径）: "
              f"{abs(r_ide.fuel_l - t_fuel) / t_fuel * 100:.4f} pp")


# ======================================================================
def build_table(params):
    specs = simulator.build_fleet(40, 2, "fuel")
    trips = [simulator.simulate_trip(s, params) for s in specs]
    rows = []
    for trip in trips:
        frame = _frame_from_truth(trip, params)
        res = energy.analyze_frame(frame, params, "fuel")
        g = preprocess.estimate_grade(frame)["grade"]
        md = modes.segment_modes(frame, g)
        ev = modes.driving_events(frame, md)
        f = features.extract_features(frame, res, md, ev, g)
        f["trip_id"] = trip.spec.trip_id
        f["injected_fault"] = trip.spec.injected_fault
        f["c_rr_scale"] = trip.spec.c_rr_scale
        f["bsfc_scale"] = trip.spec.bsfc_scale
        rows.append(f)
    return pd.DataFrame(rows)


# ======================================================================
def exp_K(df):
    hr("实验 K：核实「胎压不足与燃烧恶化的整体能效几乎相同」"
       "（README 称 0.3122 vs 0.3125）")
    for f in ("normal", "tire_pressure_low", "combustion_degraded",
              "aggressive_driving", "excessive_idling"):
        s = df.loc[df.injected_fault == f, "eta_engine_est"]
        print(f"  {f:<22} n={s.size:>3}  中位数={s.median():.4f}  "
              f"均值={s.mean():.4f}  范围 [{s.min():.4f}, {s.max():.4f}]")
    a = df.loc[df.injected_fault == "tire_pressure_low", "eta_engine_est"]
    b = df.loc[df.injected_fault == "combustion_degraded", "eta_engine_est"]
    n = df.loc[df.injected_fault == "normal", "eta_engine_est"]
    print(f"\n  胎压不足 中位数 {a.median():.4f} vs 燃烧恶化 中位数 {b.median():.4f} "
          f"→ 差 {abs(a.median() - b.median()):.4f}")
    print(f"  正常车次 中位数 {n.median():.4f}（后者与故障车差距 "
          f"{abs(n.median() - a.median()):.4f}）")
    # 判别力：能否用 eta 单独区分两类故障？
    from itertools import combinations
    lab = df["injected_fault"].to_numpy()
    sel = np.isin(lab, ["tire_pressure_low", "combustion_degraded"])
    x = df.loc[sel, "eta_engine_est"].to_numpy()
    y = (lab[sel] == "combustion_degraded").astype(int)
    # 简单阈值判别的最优准确率
    best = 0.0
    for thr in np.unique(x):
        acc = max(((x < thr) == (y == 1)).mean(), ((x >= thr) == (y == 1)).mean())
        best = max(best, acc)
    print(f"  仅用 eta_engine_est 区分这两类故障的最优准确率：{best:.1%}"
          f"（随机基线 {max(y.mean(), 1 - y.mean()):.1%}）")


# ======================================================================
def exp_L(df):
    hr("实验 L：核实「怠速改用每百公里秒数后才分得开」（README §5.3）")
    lab = df["injected_fault"].to_numpy()
    nrm = lab == "normal"
    idl = lab == "excessive_idling"
    for col, name in (("idle_time_pct", "怠速时间占比 %"),
                      ("idle_s_per_100km", "每百公里怠速秒数")):
        a = df.loc[nrm, col].to_numpy(dtype=float)
        b = df.loc[idl, col].to_numpy(dtype=float)
        print(f"  【{name}】")
        print(f"    正常车次 中位数={np.median(a):.1f}  p90={np.percentile(a, 90):.1f}  "
              f"max={a.max():.1f}")
        print(f"    注入怠速 中位数={np.median(b):.1f}  min={b.min():.1f}  "
              f"max={b.max():.1f}")
        # 重叠度
        overlap = ((a >= b.min()) & (a <= b.max())).mean()
        print(f"    正常车次落入注入怠速区间的比例（重叠）：{overlap:.1%}")
        # 用该特征做检测：阈值取正常车次的 max
        thr = a.max()
        print(f"    以 max(正常)={thr:.1f} 为阈值：检出 {int((b > thr).sum())}/{b.size} 例"
              f"，误报 {int((a > thr).sum())}/{a.size} 例\n")


# ======================================================================
def exp_M(df):
    hr("实验 M：核实「滚动阻力能耗对所有车次是常数」（README §5.5）")
    roll = df["roll_kwh_per_km"].to_numpy(dtype=float)
    mass = df["payload_t"].to_numpy(dtype=float)
    print(f"  roll_kwh_per_km      : min={roll.min():.4f} max={roll.max():.4f} "
          f"相对离散={roll.std() / roll.mean() * 100:.2f}%")
    per_t = roll / mass
    print(f"  roll_kwh_per_km / 吨 : min={per_t.min():.5f} max={per_t.max():.5f} "
          f"相对离散={per_t.std() / per_t.mean() * 100:.3f}%")
    # 理论值：每吨公里滚阻能耗 = (1000 kg)·g·C_rr·(1000 m) = g·C_rr·1e6 J
    # 换算 kWh 后 = g·C_rr·1e6 / 3.6e6
    theo = ph.G * VehicleParams().c_rr * 1e6 / 3.6e6
    print(f"  理论值 g·C_rr·1e6/3.6e6（kWh/(t·km)）: {theo:.5f}  "
          f"（= 9.80665 × 0.0075 × 1e6 / 3.6e6）")
    print(f"\n  说明：README §5.5 称「实测 crr_est 恒为 0.02040」——")
    print(f"        该数值恰好等于这里的吨公里滚阻能耗 {per_t.mean():.5f}，")
    print(f"        但 `crr_est` 这个变量在代码中从未被赋值（见实验 B），")
    print(f"        且它并不是「滚动阻力系数」而是「每吨公里滚阻能耗」。")
    print(f"  分析侧 c_rr 恒取铭牌 {VehicleParams().c_rr}，因此 roll 的离散只来自")
    print(f"  载荷与坡度 cosθ —— 单位吨公里口径下几乎恒定，")
    print(f"  **不能**作为滚阻故障的检测器（该结论本身成立）。")


def main():
    params = VehicleParams()
    exp_I(params)
    df = build_table(params)
    exp_K(df)
    exp_L(df)
    exp_M(df)


if __name__ == "__main__":
    main()
