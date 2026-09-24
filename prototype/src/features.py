"""行程级特征工程。

特征定义对**全车队**与**单车**完全一致：
生产环境中这些特征由数仓（ClickHouse/Spark）计算并落地，
原型里则由同一条流水线从报文直接算出 —— 保证训练与推理同分布。
"""

from __future__ import annotations

import numpy as np

from . import energy as en
from .preprocess import SignalFrame, GRID_HZ

SPEED_BANDS = [(0, 10), (10, 30), (30, 50), (50, 70), (70, 90), (90, 200)]


def extract_features(frame: SignalFrame, res: en.EnergyResult, modes: dict,
                     events: dict, grade: np.ndarray) -> dict:
    """抽取单个行程的特征向量。"""
    dt = 1.0 / GRID_HZ
    v_kph = np.nan_to_num(frame["WheelBasedVehicleSpeed"])
    v = v_kph / 3.6
    dist_km = max(res.distance_km, 1e-9)
    mass_t = res.diagnostics.get("mass_mean_kg", 0.0) / 1000.0

    # 「行驶时间」口径：剔除怠速。怠速会同时压低平均车速，
    # 若用全程平均车速描述工况，模型会把"怠速多"误读成"工况差"，
    # 从而把怠速油耗解释掉 —— 这是必须切断的混淆路径。
    moving = v_kph >= 1.0
    moving_time = max(float(moving.sum()) * dt, 1e-9)
    moving_km = float(v[moving].sum()) * dt / 1000.0

    f: dict[str, float] = {
        # --- 行程基本量 ---
        "distance_km": res.distance_km,
        "duration_s": res.duration_s,
        "avg_speed_kph": dist_km / max(res.duration_s / 3600.0, 1e-9),
        "avg_speed_moving_kph": moving_km / (moving_time / 3600.0),
        "max_speed_kph": float(np.max(v_kph)),
        "speed_std": float(np.std(v_kph)),
        # --- 载荷（能耗第一敏感因子） ---
        "payload_t": mass_t,
        "mass_ton_km": res.mass_ton_km,
        # --- 驾驶行为 ---
        "pke_kj_per_km": events["pke_kj_per_km"],
        "harsh_accel_per_100km": events["harsh_accel_per_100km"],
        "harsh_brake_per_100km": events["harsh_brake_per_100km"],
        "accel_std": events["accel_std"],
        "accel_p95": events["accel_p95"],
        "idle_time_pct": events["idle_time_pct"],
        "idle_events": float(events["idle_events"]),
        # 距离归一化的怠速时长：用百分比会被"行程长短"混淆
        # （激烈驾驶的坡道更短、总时长更短，怠速占比反而被动升高）。
        "idle_s_per_100km": events["idle_seconds"] / dist_km * 100.0,
        "idle_seconds": events["idle_seconds"],
        "cruise_time_pct": events["cruise_time_pct"],
        "overspeed_seconds": events["overspeed_seconds"],
        # --- 线路/地形 ---
        "grade_abs_mean": float(np.nanmean(np.abs(grade))),
        "grade_up_m": float(np.sum(np.clip(grade, 0, None) / 100.0 * v * dt)),
        "grade_down_m": float(np.sum(np.clip(grade, None, 0) / 100.0 * v * dt)),
        "grade_max": float(np.nanmax(grade)),
        # --- 环境 ---
        "ambient_c": float(np.nanmean(frame["AmbientAirTemperature"]))
        if frame.has("AmbientAirTemperature") else 20.0,
        # --- 物理能耗分量（每公里） ---
        "roll_kwh_per_km": res.wheel.get("滚动阻力", 0.0) / dist_km,
        "aero_kwh_per_km": res.wheel.get("空气阻力", 0.0) / dist_km,
        "grade_kwh_per_km": res.wheel.get("坡道(净值)", 0.0) / dist_km,
        "brake_kwh_per_km": res.e_brake_req_kwh / dist_km,
        "traction_kwh_per_km": res.e_traction_wheel_kwh / dist_km,
        # --- 工况时间占比 ---
        **{f"mode_{k}_pct": v_["time_pct"] for k, v_ in modes["shares"].items()},
    }

    # 车速区间时间占比（按**行驶时间**归一，剔除怠速影响，刻画工况结构）
    for lo, hi in SPEED_BANDS:
        sel = (v_kph >= lo) & (v_kph < hi) & moving
        f[f"speed_band_{lo}_{hi}_pct"] = float(sel.sum() * dt / moving_time * 100.0)

    # 单位吨公里能耗（归一化口径，跨载荷可比）
    f["kwh_per_100tkm"] = res.kwh_per_100tkm

    # --- 物理反标定诊断量 ---
    # 注意：滚动阻力能量本身**不能**用作滚阻故障的检测器 ——
    # 分析侧永远按铭牌 C_rr 计算，所以它对所有车次都是常数。
    # 真正的判据是"整体能效反标定 + 胎压信号"，见 ai_models。
    trac_engine = (res.chem.get("传动损失", 0.0)
                   + res.chem.get("车轮牵引能量(经传动)", 0.0))
    chem_for_traction = res.energy_kwh - res.chem.get("怠速与附件耗能", 0.0)
    f["eta_engine_est"] = trac_engine / max(chem_for_traction, 1e-9)
    # 胎压：区分"行驶阻力增大"与"动力系统效率下降"的唯一独立观测量
    tp = [np.nanmean(frame[n]) for n in ("TirePressureFront", "TirePressureRear")
          if frame.has(n)]
    f["tire_pressure_kpa"] = float(np.mean(tp)) if tp else float("nan")
    return f


# 特征分组 —— 决定了"残差代表什么"，是本方案 AI 设计的核心。
#
# LEAKY_FEATURES：由能耗/物理分解直接推导出来的量。它们是"答案的一部分"，
#   放进模型会造成循环论证（模型用物理模型的结果去预测油耗），必须排除。
LEAKY_FEATURES = {
    "kwh_per_100tkm", "mass_ton_km",
    "roll_kwh_per_km", "aero_kwh_per_km", "grade_kwh_per_km",
    "brake_kwh_per_km", "traction_kwh_per_km",
    "crr_est", "cda_est", "eta_engine_est",   # 物理反标定量：用于规则诊断，不作预测特征
    "tire_pressure_kpa",                       # 直接观测量，不能作为"预测油耗"的特征
}

# DRIVING_FEATURES：描述"司机怎么开 / 这趟活儿干得怎么样"的量。
#   只在「作业基线模型」中排除 —— 于是：
#     残差(作业基线) = 驾驶行为 + 车辆状况 + 其他超额消耗
#   责任归属再由物理规则判据区分（见 ai_models.detect_faults），
#   这直接回答了业务最关心的问题："这台车费油，是车的问题还是司机的问题？"
#
#   注意：duration_s / avg_speed_kph / 怠速时间占比 / 工况时间占比 都会随怠速膨胀，
#   必须排除，否则基线会把怠速油耗当成"工况差"解释掉。
DRIVING_FEATURES = {
    "pke_kj_per_km", "harsh_accel_per_100km", "harsh_brake_per_100km",
    "accel_std", "accel_p95", "speed_std", "idle_time_pct", "idle_events",
    "idle_s_per_100km", "idle_seconds", "overspeed_seconds",
    "duration_s", "avg_speed_kph",
    # 车速选择属于驾驶员决策，不属于"这趟活儿"：
    # 卡车激烈驾驶的主要能耗代价是超速（风阻 ∝ v²），若把它算进作业基线，
    # 模型会把"开得快"解释成"应该的"，超速造成的浪费就永远查不出来。
    "avg_speed_moving_kph", "max_speed_kph",
}

# 动态生成的工况占比列（mode_xxx_pct）同样属于"怎么开"
DRIVING_PREFIXES = ("mode_",)

TARGET_FUEL = "l_per_100km"
TARGET_EV = "kwh_per_100km"
