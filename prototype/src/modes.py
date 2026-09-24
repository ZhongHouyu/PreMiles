"""工况切分与驾驶行为量化。

对应方案文档 4.5 节。规则状态机（可解释、可审计）为主，
后续可用无监督聚类发现未知工况（生产线扩展）。
"""

from __future__ import annotations

import numpy as np

from .preprocess import SignalFrame, GRID_HZ, moving_average

# 工况标签（按优先级从上到下判定）
MODE_ORDER = ["停车熄火", "怠速", "起步加速", "制动减速", "爬坡", "下坡", "匀速巡航", "低速行驶"]

# 驾驶行为事件阈值
HARSH_ACCEL = 0.8      # m/s^2
HARSH_BRAKE = -1.2     # m/s^2
IDLE_SPEED = 1.0       # km/h


def segment_modes(frame: SignalFrame, grade: np.ndarray,
                  brake_signal: np.ndarray | None = None,
                  accel_smooth_s: float = 1.0) -> dict:
    """逐样本打工况标签，并汇总时间/里程/能耗占比。"""
    dt = 1.0 / GRID_HZ
    v_kph = np.nan_to_num(frame["WheelBasedVehicleSpeed"])
    v = v_kph / 3.6
    v_s = moving_average(v, max(1, int(accel_smooth_s * GRID_HZ)))
    a = np.gradient(v_s, dt)

    fuel = np.nan_to_num(frame["EngineFuelRate"]) if frame.has("EngineFuelRate") else None
    if brake_signal is None and frame.has("BrakeSwitch"):
        brake_signal = np.nan_to_num(frame["BrakeSwitch"]) > 0.5
    if brake_signal is None:
        brake_signal = a < -0.3

    engine_on = (fuel > 0.3) if fuel is not None else (v_kph > 1.0)

    label = np.full(v.size, "低速行驶", dtype=object)
    # 优先级：从低到高依次覆盖
    label[v_kph > 3.0] = "低速行驶"
    label[(np.abs(a) <= 0.3) & (v_kph > 40.0)] = "匀速巡航"
    label[grade < -1.5] = "下坡"
    label[grade > 1.5] = "爬坡"
    label[a < -0.3] = "制动减速"
    label[brake_signal] = "制动减速"
    label[a > 0.3] = "起步加速"
    label[(v_kph < IDLE_SPEED) & engine_on] = "怠速"
    label[(v_kph < IDLE_SPEED) & (~engine_on)] = "停车熄火"

    total_t = v.size * dt
    dist = v * dt
    out = {}
    for m in MODE_ORDER:
        sel = label == m
        if not sel.any():
            out[m] = dict(seconds=0.0, time_pct=0.0, km=0.0, km_pct=0.0, kwh=0.0, kwh_pct=0.0)
            continue
        e = 0.0
        if fuel is not None:
            e = float(np.sum(fuel[sel]) * dt / 3600.0) * 9.9867
        out[m] = dict(seconds=float(sel.sum() * dt),
                      time_pct=float(sel.sum() / v.size * 100.0),
                      km=float(dist[sel].sum() / 1000.0),
                      km_pct=float(dist[sel].sum() / max(dist.sum(), 1e-9) * 100.0),
                      kwh=e, kwh_pct=0.0)
    e_tot = sum(x["kwh"] for x in out.values())
    for m in out:
        out[m]["kwh_pct"] = out[m]["kwh"] / e_tot * 100.0 if e_tot > 0 else 0.0

    return dict(labels=label, shares=out, accel=a, speed_mps=v_s,
                time_s=total_t, distance_km=float(dist.sum() / 1000.0))


def driving_events(frame: SignalFrame, modes: dict) -> dict:
    """急加速/急减速/怠速事件统计 —— 驾驶行为画像的基础。"""
    dt = 1.0 / GRID_HZ
    a = modes["accel"]
    v_kph = np.nan_to_num(frame["WheelBasedVehicleSpeed"])
    dist_km = max(modes["distance_km"], 1e-9)

    def count_runs(mask: np.ndarray, min_gap_s: float = 2.0) -> int:
        """把连续满足条件的采样合并成一个事件（间隔 > min_gap 才算新事件）。"""
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return 0
        gaps = np.diff(idx) * dt
        return int(1 + np.sum(gaps > min_gap_s))

    n_acc = count_runs(a > HARSH_ACCEL)
    n_brk = count_runs(a < HARSH_BRAKE)
    idle_mask = v_kph < IDLE_SPEED
    n_idle = count_runs(idle_mask, min_gap_s=30.0)

    # PKE：单位里程的正动能需求（Positive Kinetic Energy），经典驾驶激烈度指标
    pke = float(np.sum(np.clip(modes["speed_mps"], 0, None) * np.clip(a, 0, None) * dt)
                / dist_km) / 1000.0

    return dict(
        harsh_accel=n_acc, harsh_brake=n_brk,
        harsh_accel_per_100km=n_acc / dist_km * 100.0,
        harsh_brake_per_100km=n_brk / dist_km * 100.0,
        idle_events=n_idle,
        idle_seconds=float(idle_mask.sum() * dt),
        idle_time_pct=float(idle_mask.sum() / a.size * 100.0),
        pke_kj_per_km=pke,
        accel_std=float(np.std(a)),
        accel_p95=float(np.percentile(np.abs(a), 95)),
        overspeed_seconds=float(np.sum(v_kph > 90.0) * dt),
        cruise_time_pct=float(np.sum((np.abs(a) <= 0.3) & (v_kph > 40.0)) / a.size * 100.0),
    )


def driver_score(events: dict, residual_pct: float,
                 cohort: dict | None = None) -> dict:
    """驾驶行为评分（0~100）+ 扣分归因。

    与"车队同线路同车型基线"对比，而不是绝对阈值 —— 保证公平可比。
    """
    c = cohort or {}
    w_res = 45.0
    w_acc = 18.0
    w_brk = 17.0
    w_idle = 12.0
    w_cruise = 8.0

    def norm(x, base, scale):
        return float(np.clip((x - base) / max(scale, 1e-9), 0.0, 1.0))

    p_res = w_res * norm(residual_pct, c.get("residual_p50", 0.0),
                         c.get("residual_iqr", 10.0))
    p_acc = w_acc * norm(events["harsh_accel_per_100km"],
                         c.get("accel_p50", 1.0), c.get("accel_iqr", 3.0))
    p_brk = w_brk * norm(events["harsh_brake_per_100km"],
                         c.get("brake_p50", 1.0), c.get("brake_iqr", 3.0))
    p_idle = w_idle * norm(events["idle_time_pct"],
                           c.get("idle_p50", 4.0), c.get("idle_iqr", 8.0))
    p_cruise = w_cruise * norm(100.0 - events["cruise_time_pct"],
                               c.get("noncruise_p50", 45.0), c.get("noncruise_iqr", 25.0))
    total = 100.0 - (p_res + p_acc + p_brk + p_idle + p_cruise)

    return dict(
        score=float(np.clip(total, 0.0, 100.0)),
        deductions={
            "能耗高于基线": p_res, "急加速偏多": p_acc, "急减速偏多": p_brk,
            "怠速时间偏长": p_idle, "经济车速占比偏低": p_cruise,
        },
    )
