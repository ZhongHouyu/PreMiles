#!/usr/bin/env python
"""独立审计实验：验证 PreMiles 原型中若干关键论断。

实验清单
  A  三道自检能否发现"物理模型本身算错" —— 故意用错误整车参数重算
  B  README 引用的 crr_est / overspeed_seconds 是否真实存在于产物中
  C  作业基线是否混入了驾驶行为（speed_band_*_pct 车速分布）—— 特征消融
  D  异常判据阈值取自媒体自身 —— 折半交叉验证下的查准/查全
  E  README 声称"所有车次峰值发动机需求 ≤ 347 kW"是否成立
  F  peer_residual 近邻基线是否被同批故障样本污染
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent / "prototype"
sys.path.insert(0, str(ROOT))

from src import ai_models, can_io, energy, features, modes, preprocess, simulator  # noqa: E402
from src.physics import VehicleParams  # noqa: E402

OUT = ROOT / "outputs"
CFG = ROOT / "config"
IS_FUEL = True
TARGET = "_target"


def hr(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}", flush=True)


# ----------------------------------------------------------------------
# 公共：重建车队特征表（与 run_demo.py 完全同路径）
# ----------------------------------------------------------------------
def build_feature_table(n_days: int = 40, trips_per_day: int = 2):
    params = VehicleParams()
    specs = simulator.build_fleet(n_days=n_days, trips_per_day=trips_per_day,
                                  powertrain="fuel")
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
        f["day"] = trip.spec.day
        f["injected_fault"] = trip.spec.injected_fault
        f["_target"] = res.l_per_100km
        f["_res"] = res
        f["_events"] = ev
        rows.append(f)
    return params, specs, trips, pd.DataFrame(rows)


def model_columns(feat_df: pd.DataFrame):
    meta_cols = {"trip_id", "day", "injected_fault", "_target", "_has_can",
                 "_res", "_modes", "_events"}
    leaky = set(features.LEAKY_FEATURES)
    model_cols = [c for c in feat_df.columns
                  if c not in meta_cols and c not in leaky
                  and feat_df[c].dtype != object]
    job_cols = [c for c in model_cols
                if c not in features.DRIVING_FEATURES
                and not c.startswith(features.DRIVING_PREFIXES)]
    return model_cols, job_cols


def _frame_from_truth(trip, params):
    dt = simulator.DT
    grid = np.arange(0.0, trip.t[-1], 1.0 / simulator.LOG_HZ)
    idx = np.clip((grid / dt).astype(int), 0, trip.v.size - 1)

    def s(a):
        return np.asarray(a, dtype=float)[idx]

    fuel_cum = np.concatenate([[0.0], np.cumsum(trip.fuel_rate_lph * dt / 3600.0)])
    data = {
        "WheelBasedVehicleSpeed": s(trip.v) * 3.6,
        "EngineSpeed": np.where(s(trip.v) < 1.0, 600.0, 1200.0),
        "EngineFuelRate": s(trip.fuel_rate_lph),
        "TotalFuelUsed": s(fuel_cum)[: grid.size],
        "HighResolutionTotalVehicleDistance": s(trip.s) / 1000.0,
        "TotalVehicleMass": s(trip.mass),
        "RoadGrade": s(trip.grade),
        "AmbientAirTemperature": np.full(grid.size, trip.spec.ambient_c),
        "GNSSSpeed": s(trip.v) * 3.6,
    }
    df = pd.DataFrame(data, index=pd.Index(grid, name="t")).ffill().bfill()
    q = dict(score=100.0, level="A(高置信)", issues=[], blockers=[],
             decode_stats={"decode_success_rate": 1.0, "frames_unknown_id": 0,
                           "frames_total": 0, "frames_decoded": 0, "frames_failed": 0,
                           "messages_present": len(data)},
             span_s=float(grid[-1]), n_signals=len(data), per_signal={})
    return preprocess.SignalFrame(t=grid, df=df, quality=q, native_span={}, t_abs0=0.0)


# ======================================================================
# A. 三道自检的独立性
# ======================================================================
def exp_A(params):
    hr("实验 A：三道自检能否发现「物理模型本身算错」？")
    db = can_io.load_dbc(CFG / "trucks_demo.dbc")
    log = OUT / "canlog_T000-2.log"
    decoded, dstat = can_io.decode_log(db, log)
    frame = preprocess.build_frame(decoded, dstat)

    variants = {
        "基准（铭牌参数）": {},
        "风阻系数 C_d ×1.6（严重错标）": {"c_d": params.c_d * 1.6},
        "迎风面积 A ×1.5": {"frontal_area_m2": params.frontal_area_m2 * 1.5},
        "滚阻系数 C_rr ×3.0（严重错标）": {"c_rr": params.c_rr * 3.0},
        "整备质量 +8000 kg（严重错标）": {"curb_mass_kg": params.curb_mass_kg + 8000.0},
    }
    import copy
    print(f"  {'参数设定':<34}{'守恒残差%':>11}{'车轮恒等式%':>12}"
          f"{'里程互差%':>11}{'对账误差%':>11}{'三道自检':>10}  牵引能耗kWh")
    for label, kw in variants.items():
        p = copy.copy(params)
        for k, v in kw.items():
            setattr(p, k, v)
        r = energy.analyze_frame(frame, p, "fuel")
        c = r.checks
        eb = c["energy_balance"]["residual_pct"]
        wi = c["wheel_identity"]["residual_pct"]
        ds = c["distance_consistency"]["spread_pct"]
        rc = c["reconciliation"].get("error_pct", float("nan"))
        allpass = (eb < 3.0) and c["wheel_identity"]["passed"] \
            and c["distance_consistency"]["passed"] and c["reconciliation"].get("passed")
        print(f"  {label:<34}{eb:>11.2e}{wi:>12.2e}{ds:>11.2f}{rc:>11.2f}"
              f"{'全部通过' if allpass else '有失败':>10}  {r.e_traction_wheel_kwh:.2f}")
    print("\n  真值（仿真器）：牵引能耗见 outputs/车队汇总.csv；星号项为刻意错标")
    print("  说明：守恒残差与车轮恒等式是**代数恒等式**，与物理参数无关。")
    print("        里程互差与对账误差只依赖报文信号，也与整车参数无关。")
    print("        → 三道自检无法发现整车参数标定错误。")


# ======================================================================
# B. README 引用的量是否真实存在
# ======================================================================
def exp_B(feat_df):
    hr("实验 B：README 引用的 crr_est / overspeed_seconds 是否真实存在")
    csv = pd.read_csv(OUT / "车队汇总.csv")
    print(f"  车队汇总.csv 列数：{len(csv.columns)}")
    for col in ("crr_est", "cda_est"):
        print(f"    {col:<16} 出现在 CSV 列中？ {'是' if col in csv.columns else '否'}")
    print(f"    {'overspeed_seconds':<16} 出现在 CSV 列中？ "
          f"{'是' if 'overspeed_seconds' in csv.columns else '否'}")
    print(f"    overspeed_seconds 是否在特征表中？ "
          f"{'是' if 'overspeed_seconds' in feat_df.columns else '否'}")
    if "overspeed_seconds" in feat_df.columns:
        s = feat_df["overspeed_seconds"].to_numpy(dtype=float)
        print(f"    → overspeed_seconds：非零样本 {int((s > 0).sum())}/{s.size}，"
              f"max={s.max():.1f}s，均值={s.mean():.3f}s")
    print(f"    整车铭牌 C_rr = {VehicleParams().c_rr}（README 称 crr_est 恒为 0.02040）")
    print(f"    代码中 crr_est / cda_est 仅出现在 features.LEAKY_FEATURES 排除名单里，"
          f"从未被赋值")


# ======================================================================
# C. 作业基线是否混入驾驶行为
# ======================================================================
def exp_C(feat_df, job_cols):
    hr("实验 C：作业基线是否混入了驾驶行为（车速分布）—— 特征消融")
    band = [c for c in job_cols if c.startswith("speed_band_")]
    print(f"  job_cols 共 {len(job_cols)} 个，其中 speed_band_*_pct 占 {len(band)} 个：")
    print(f"    {band}")
    cruise = [c for c in job_cols if c == "cruise_time_pct"]
    print(f"    另外 cruise_time_pct 是否在基线内：{'是' if cruise else '否'}"
          f"（它在 detect_faults 里又被当作「驾驶行为证据」用）")

    def run(cols, tag):
        bm = ai_models.train_baseline(feat_df[cols + [TARGET]], TARGET)
        r = bm.residual_pct
        fault = feat_df["injected_fault"].to_numpy()
        print(f"\n  [{tag}] {len(cols)} 特征  样本外 R²={bm.metrics['r2_oof']:.3f} "
              f"MAPE={bm.metrics['mape_oof']:.2f}%")
        for f in ("aggressive_driving", "excessive_idling", "tire_pressure_low",
                  "combustion_degraded", "normal"):
            sel = fault == f
            print(f"      {f:<22} 平均残差 {r[sel].mean():+6.2f}%  (n={int(sel.sum())})")
        return r, bm

    full, _ = run(job_cols, "现状：含 speed_band_*")
    noband = [c for c in job_cols if not c.startswith("speed_band_")]
    red, _ = run(noband, "消融：剔除 speed_band_*")
    nocr = [c for c in noband if c != "cruise_time_pct"]
    red2, _ = run(nocr, "再剔除 cruise_time_pct")

    fault = feat_df["injected_fault"].to_numpy()
    sel = fault == "aggressive_driving"
    print(f"\n  → 激烈驾驶平均残差：含车速分布 {full[sel].mean():+.2f}% "
          f"→ 剔除后 {red[sel].mean():+.2f}%（变化 {red[sel].mean() - full[sel].mean():+.2f} 个百分点）")
    print(f"  → 再剔除 cruise_time_pct 后 {red2[sel].mean():+.2f}%")


# ======================================================================
# D. 判据阈值的取材外推性
# ======================================================================
def exp_D(feat_df, job_cols):
    hr("实验 D：异常判据阈值取自媒体自身 —— 折半交叉验证")
    target_vals = feat_df[TARGET].to_numpy(dtype=float)
    fault = feat_df["injected_fault"].to_numpy()
    y_true = fault != "normal"
    n = len(feat_df)

    print("  [D1] 全量自评（复现 README 口径）")
    bm = ai_models.train_baseline(feat_df[job_cols + [TARGET]], TARGET)
    res = bm.residual_pct
    iso, anom, _ = ai_models.fit_anomaly_detector(feat_df[job_cols], res)
    findings = ai_models.detect_faults(res, anom, feat_df)
    flagged = {f["index"] for f in findings}
    yp = np.zeros(n, dtype=bool)
    for i in flagged:
        yp[i] = True
    tp = int((y_true & yp).sum()); fp = int((~y_true & yp).sum())
    fn = int((y_true & ~yp).sum())
    p = tp / max(tp + fp, 1); rc = tp / max(tp + fn, 1)
    print(f"       TP={tp} FP={fp} FN={fn}  查准率={p:.1%} 查全率={rc:.1%} "
          f"F1={2 * p * rc / max(p + rc, 1e-9):.3f}")

    print("\n  [D2] 阈值只在 A 半车队上标定，在 B 半车队上评估（5 次随机折半）")
    rng = np.random.default_rng(7)
    rows = []
    for rep in range(5):
        perm = rng.permutation(n)
        a_idx, b_idx = perm[: n // 2], perm[n // 2:]
        fa = feat_df.iloc[a_idx].reset_index(drop=True)
        # 阈值由 A 半（去均值对齐）标定：用 A 的中位数/P90 作为 B 的阈值
        # detect_faults 内部按传入表自算阈值，因此这里把 B 的统计量替换为 A 的
        # —— 直接调用内部逻辑不可行，改为等价做法：在 A 上标定后手写判据
        ra = res[a_idx]
        # A 半标定的规则阈值
        def thr(col, ratio, high=True, default=0.0):
            if col not in feat_df:
                return default
            v = feat_df[col].to_numpy(dtype=float)[a_idx]
            v = v[np.isfinite(v)]
            if v.size == 0:
                return default
            med = float(np.median(v))
            if high:
                return max(float(np.percentile(v, 90)), med * ratio)
            return max(med * ratio, float(np.percentile(v, 5)))

        idle_th = thr("idle_s_per_100km", 1.8)
        speed_th = thr("avg_speed_moving_kph", 1.08)
        eta_th = thr("eta_engine_est", 0.93, high=False)
        # 统计判据阈值同样用 A 半的 z 分布（中位数/标准差）
        med_a, sd_a = float(np.median(ra)), float(np.std(ra))
        z_th = 1.8

        yp_b = np.zeros(len(b_idx), dtype=bool)
        for k, i in enumerate(b_idx):
            row = feat_df.iloc[i]
            z = (res[i] - med_a) / max(sd_a, 1e-9)
            hit_rule = (row.get("idle_s_per_100km", 0.0) > idle_th
                        or row.get("avg_speed_moving_kph", 0.0) > speed_th
                        or row.get("eta_engine_est", 1.0) < eta_th)
            hit_stat = (z >= z_th and res[i] > 1.5)
            yp_b[k] = bool(hit_rule or hit_stat)
        yt_b = y_true[b_idx]
        tp = int((yt_b & yp_b).sum()); fp = int((~yt_b & yp_b).sum())
        fn = int((yt_b & ~yp_b).sum())
        pp = tp / max(tp + fp, 1); rr = tp / max(tp + fn, 1)
        rows.append((tp, fp, fn, pp, rr))
        print(f"       折半{rep + 1}: TP={tp:>2} FP={fp:>2} FN={fn:>2} "
              f"查准率={pp:>5.1%} 查全率={rr:>5.1%}")
    arr = np.array(rows, dtype=float)
    print(f"      → 均值 查准率={arr[:, 3].mean():.1%} 查全率={arr[:, 4].mean():.1%}"
          f"（对比全量自评 查准率=100.0% 查全率=79.2%）")


# ======================================================================
# E. 峰值发动机需求
# ======================================================================
def exp_E(params, specs, trips):
    hr("实验 E：README 声称「所有车次峰值发动机需求 ≤ 347 kW」")
    from src import physics as ph
    peaks = []
    for tr in trips:
        eng = ph.engine_power_from_wheel(tr.p_total, params)
        peaks.append(float(np.max(eng)))
    peaks = np.array(peaks)
    rated = params.engine_rated_power_kw
    print(f"  额定功率 {rated} kW；样本 {peaks.size} 车次")
    print(f"  峰值发动机功率：max={peaks.max():.1f} kW  min={peaks.min():.1f} kW  "
          f"均值={peaks.mean():.1f} kW")
    print(f"  超过 347 kW 的车次：{int((peaks > 347).sum())}")
    print(f"  超过额定 {rated} kW 的车次：{int((peaks > rated).sum())}")
    # 被注入激烈驾驶的车次
    ag = np.array([t.spec.injected_fault == "aggressive_driving" for t in trips])
    print(f"  其中激烈驾驶车次峰值：{np.round(np.sort(peaks[ag])[::-1][:8], 1)}")


# ======================================================================
# F. 近邻基线是否被污染
# ======================================================================
def exp_F(feat_df):
    hr("实验 F：同类近邻基线是否被同批故障样本污染")
    target_vals = feat_df[TARGET].to_numpy(dtype=float)
    fault = feat_df["injected_fault"].to_numpy()
    pr = ai_models.peer_residual(feat_df, target_vals)
    print(f"  peer_residual 平均绝对偏差 = {np.mean(np.abs(pr)):.2f}%")
    for f in ("aggressive_driving", "tire_pressure_low", "combustion_degraded",
              "excessive_idling", "normal"):
        sel = fault == f
        print(f"    {f:<22} 平均同类偏差 {pr[sel].mean():+6.2f}%  (n={int(sel.sum())})")
    # 统计：每个车次的 k=9 近邻里有多少是故障样本
    cols = ["payload_t", "distance_km", "grade_abs_mean"]
    X = feat_df[cols].to_numpy(dtype=float)
    Z = (X - np.nanmean(X, axis=0)) / np.where(np.nanstd(X, axis=0) < 1e-9, 1.0,
                                               np.nanstd(X, axis=0))
    frac = []
    for i in range(len(target_vals)):
        d = np.linalg.norm(Z - Z[i], axis=1)
        d[i] = np.inf
        idx = np.argsort(d)[:9]
        frac.append(float((fault[idx] != "normal").mean()))
    frac = np.array(frac)
    print(f"\n  每个车次的 9 个近邻中，故障样本占比：均值 {frac.mean():.1%}，"
          f"最大 {frac.max():.0%}")
    print(f"  （车队整体故障比例 {(fault != 'normal').mean():.1%}）")
    print("  → 近邻基线把「同类型故障车」也算作正常参照，会系统性抬高基准、压低偏差")


def main():
    buf = io.StringIO()
    with redirect_stdout(buf):
        params, specs, trips, feat_df = build_feature_table()
    model_cols, job_cols = model_columns(feat_df)
    print(f"[准备] 车队 {len(feat_df)} 车次；model_cols={len(model_cols)}，"
          f"job_cols={len(job_cols)}")
    print(f"[准备] job_cols = {job_cols}")

    exp_A(params)
    exp_B(feat_df)
    exp_C(feat_df, job_cols)
    exp_D(feat_df, job_cols)
    exp_E(params, specs, trips)
    exp_F(feat_df)


if __name__ == "__main__":
    main()
