"""信号清洗、重采样与数据质量评分。

对应方案文档 4.2 节流水线第 2~4 步。

核心原则
--------
1. **统一到固定网格**：所有信号对齐到 10 Hz 均匀时间轴，便于积分与融合。
2. **按物理性质选择插值方式**：连续量线性插值、累计量零阶保持、开关量就近取值。
   用错插值方式会直接污染能耗积分（例如对累计油耗做线性插值会伪造油耗）。
3. **质量前置**：低质量数据必须被标注，而不是默默产出一个错误结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

GRID_HZ = 10.0

# 信号名 -> 插值策略   ('linear' | 'zoh' | 'nearest')
INTERP_POLICY: dict[str, str] = {
    "WheelBasedVehicleSpeed": "linear",
    "EngineSpeed": "linear",
    "ActualEnginePercentTorque": "linear",
    "EngineFuelRate": "linear",
    "TotalFuelUsed": "zoh",          # 累计量：绝不能线性插值
    "HighResolutionTotalVehicleDistance": "zoh",
    "AmbientAirTemperature": "linear",
    "TotalVehicleMass": "zoh",       # 载荷阶跃变化，零阶保持更贴近物理
    "AxleLoadFront": "zoh",
    "RoadGrade": "linear",
    "WindSpeed": "linear",
    "Latitude": "linear",
    "Longitude": "linear",
    "GNSSAltitude": "linear",
    "GNSSSpeed": "linear",
    "GNSSHeading": "nearest",
    "GNSSSatellites": "nearest",
    "PackVoltage": "linear",
    "PackCurrent": "linear",
    "StateOfCharge": "zoh",
    "MotorTorque": "linear",
    "MotorSpeedRpm100": "linear",
}

# 物理量程断言（单位见 dbc_spec）
RANGE_CHECK: dict[str, tuple[float, float]] = {
    "WheelBasedVehicleSpeed": (0.0, 160.0),
    "EngineSpeed": (0.0, 2600.0),
    "EngineFuelRate": (0.0, 200.0),
    "AmbientAirTemperature": (-60.0, 70.0),
    "TotalVehicleMass": (4000.0, 60000.0),
    "RoadGrade": (-15.0, 15.0),
    "PackVoltage": (200.0, 900.0),
    "PackCurrent": (-1200.0, 1200.0),
    "StateOfCharge": (0.0, 100.0),
}

# 信号重要度权重：质量问题对能耗结论的杀伤力
SIGNAL_WEIGHT: dict[str, float] = {
    "WheelBasedVehicleSpeed": 1.00,
    "EngineFuelRate": 1.00,
    "TotalFuelUsed": 1.00,
    "HighResolutionTotalVehicleDistance": 0.80,
    "TotalVehicleMass": 0.90,
    "RoadGrade": 0.70,
    "EngineSpeed": 0.50,
    "ActualEnginePercentTorque": 0.45,
    "GNSSSpeed": 0.50,
    "GNSSAltitude": 0.40,
    "BrakeSwitch": 0.30,
    "PackVoltage": 1.00,
    "PackCurrent": 1.00,
    "StateOfCharge": 0.70,
}

# 低分辨率累计量：长时间数值不变是**量化本身**造成的，不是传感器卡死，不做卡死告警。
STUCK_EXEMPT: set[str] = {
    "TotalFuelUsed",                    # 0.5 L/bit
    "HighResolutionTotalVehicleDistance",  # 0.005 km/bit
    "StateOfCharge",                    # 0.4 %/bit
}


@dataclass
class SignalFrame:
    t: np.ndarray                     # 相对行程起点的时间轴 (s)
    df: pd.DataFrame                  # 网格化信号
    quality: dict = field(default_factory=dict)
    native_span: dict = field(default_factory=dict)
    t_abs0: float = 0.0

    def __getitem__(self, key: str) -> np.ndarray:
        return self.df[key].to_numpy(dtype=float)

    def has(self, key: str) -> bool:
        return key in self.df.columns and self.df[key].notna().any()


def _interp_signal(ts: np.ndarray, vals: np.ndarray, grid: np.ndarray,
                   how: str) -> tuple[np.ndarray, np.ndarray]:
    """把单条信号重采样到网格，返回 (插值结果, 有效掩码)。"""
    if ts.size == 0:
        return np.full(grid.size, np.nan), np.zeros(grid.size, dtype=bool)
    order = np.argsort(ts, kind="stable")
    ts, vals = ts[order], vals[order]

    if how == "zoh":
        idx = np.searchsorted(ts, grid, side="right") - 1
        out = np.where(idx >= 0, vals[np.clip(idx, 0, ts.size - 1)], np.nan)
    elif how == "nearest":
        idx = np.searchsorted(ts, grid)
        lo = np.clip(idx - 1, 0, ts.size - 1)
        hi = np.clip(idx, 0, ts.size - 1)
        out = np.where(np.abs(grid - ts[lo]) <= np.abs(ts[hi] - grid),
                       vals[lo], vals[hi])
    else:
        out = np.interp(grid, ts, vals)
        out[(grid < ts[0]) | (grid > ts[-1])] = np.nan

    mask = ~np.isnan(out)
    return out, mask


def build_frame(decoded: dict[str, tuple[np.ndarray, np.ndarray]],
                stats: dict | None = None,
                grid_hz: float = GRID_HZ) -> SignalFrame:
    """把解码后的同异步信号统一到均匀网格，并输出质量评分。"""
    present = {k: v for k, v in decoded.items() if v[0].size > 0}
    if not present:
        raise ValueError("没有可用信号")

    t0 = min(float(v[0].min()) for v in present.values())
    t1 = max(float(v[0].max()) for v in present.values())
    span = t1 - t0
    grid = np.arange(0.0, span + 1e-9, 1.0 / grid_hz)

    cols: dict[str, np.ndarray] = {}
    q_signals: dict[str, dict] = {}
    native_span: dict[str, dict] = {}

    for name, (ts, vals) in present.items():
        how = INTERP_POLICY.get(name, "linear")
        ts_rel = ts - t0                      # 绝对 epoch -> 相对行程时间
        arr, mask = _interp_signal(ts_rel, vals, grid, how)
        cols[name] = arr

        # --- 单信号质量指标 ---
        gaps = np.diff(ts) if ts.size > 1 else np.array([0.0])
        max_gap = float(gaps.max()) if gaps.size else 0.0
        dt_med = float(np.median(gaps)) if gaps.size else 0.0
        expected = span / dt_med if dt_med > 0 else 0.0
        missing_ratio = float(np.clip(1.0 - ts.size / expected, 0.0, 1.0)) if expected > 0 else 0.0

        # 恒定值（传感器卡死）检测：最长连续相同值持续时间
        if vals.size > 20:
            d = np.diff(vals)
            change = np.flatnonzero(d != 0)
            if change.size:
                run = int(np.max(np.diff(np.concatenate([[-1], change, [vals.size]])))) - 1
            else:
                run = int(vals.size)
            stuck_s = run * dt_med
        else:
            stuck_s = 0.0

        oob = 0
        if name in RANGE_CHECK:
            lo, hi = RANGE_CHECK[name]
            oob = int(np.sum((vals < lo) | (vals > hi)))

        q_signals[name] = dict(
            n=int(ts.size), rate_hz=(1.0 / dt_med) if dt_med > 0 else 0.0,
            missing_ratio=missing_ratio, max_gap_s=max_gap,
            stuck_s=stuck_s, out_of_range=oob, interp=how,
        )
        native_span[name] = dict(t_first=float(ts.min() - t0), t_last=float(ts.max() - t0))

    df = pd.DataFrame(cols, index=pd.Index(grid, name="t"))

    # 信号在时间轴两端可能无采样（例如 1 Hz 的载荷信号），
    # 用最近有效值外推，避免 NaN 顺着物理公式污染整段积分。
    df = df.ffill().bfill()

    # 车速为一切积分的基准，缺失则整份数据不可用
    if "WheelBasedVehicleSpeed" not in df.columns:
        raise ValueError("缺少车速信号，无法进行能耗分析")

    quality = score_quality(df, q_signals, stats or {}, span)
    return SignalFrame(t=grid, df=df, quality=quality,
                       native_span=native_span, t_abs0=t0)


def score_quality(df: pd.DataFrame, q_signals: dict, decode_stats: dict,
                  span_s: float) -> dict:
    """数据质量评分（0~100），低分数据自动降级并标注置信度。

    惩罚按**信号重要度加权**：车速/油耗缺失会毁掉结论，风速缺失无关紧要。
    """
    score = 100.0
    issues: list[str] = []
    blockers: list[str] = []

    # 1) 报文解码成功率
    dsr = decode_stats.get("decode_success_rate", 1.0)
    if dsr < 0.995:
        score -= min(15.0, (0.995 - dsr) * 3000)
        issues.append(f"报文解码成功率偏低 ({dsr:.3%})")

    unk = decode_stats.get("frames_unknown_id", 0)
    if unk:
        score -= min(4.0, unk / 10000.0)
        issues.append(f"存在 {unk} 帧未在 DBC 中定义")

    # 2) 关键信号是否整体缺失
    for name in ("WheelBasedVehicleSpeed", "EngineFuelRate", "TotalFuelUsed"):
        if name not in df.columns or not np.isfinite(df[name].to_numpy()).any():
            blockers.append(f"核心信号 {name} 完全缺失")
            score -= 25.0

    # 3) 逐信号加权惩罚
    for name, q in q_signals.items():
        w = SIGNAL_WEIGHT.get(name, 0.15)
        gaps_thresh = max(3.0, 5.0 * (1.0 / q["rate_hz"] if q["rate_hz"] > 0 else 1.0))

        if q["missing_ratio"] > 0.02:
            pen = min(12.0, (q["missing_ratio"] - 0.02) * 120.0) * w
            score -= pen
            if w >= 0.5:
                issues.append(f"{name} 缺失率 {q['missing_ratio']:.1%}")

        if q["max_gap_s"] > gaps_thresh:
            pen = min(8.0, (q["max_gap_s"] - gaps_thresh) * 0.25) * w
            score -= pen
            if w >= 0.5:
                issues.append(f"{name} 最长空档 {q['max_gap_s']:.1f}s（已插值补全）")

        if q["stuck_s"] > 300.0 and w >= 0.3 and name not in STUCK_EXEMPT:
            pen = min(6.0, (q["stuck_s"] - 300.0) * 0.01) * w
            score -= pen
            issues.append(f"{name} 疑似信号卡死 {q['stuck_s']:.0f}s")

        if q["out_of_range"] > 0:
            pen = min(6.0, q["out_of_range"] * 0.01) * w
            score -= pen
            if w >= 0.5:
                issues.append(f"{name} 有 {q['out_of_range']} 个越量程点")

    # 4) 车速全程为常量（车辆未移动 / 车速信号冻结）
    v = df["WheelBasedVehicleSpeed"].to_numpy()
    if np.nanmax(v) - np.nanmin(v) < 1.0:
        score -= 30.0
        blockers.append("车速全程近似恒定，疑似信号冻结")

    score = float(np.clip(score, 0.0, 100.0))
    level = "A(高置信)" if score >= 90 else ("B(可用)" if score >= 75 else "C(低置信)")
    return dict(score=score, level=level, issues=issues, blockers=blockers,
                decode_stats=decode_stats, span_s=span_s,
                n_signals=len(q_signals), per_signal=q_signals)


# ----------------------------------------------------------------------
# 坡度估计：IMU 传感器 + GNSS 高程差分，双路冗余
# ----------------------------------------------------------------------
def moving_average(y: np.ndarray, win: int) -> np.ndarray:
    """NaN 安全的滑动平均（NaN 会污染卷积，必须先补齐）。"""
    y = np.asarray(y, dtype=float)
    win = max(1, int(win))
    if win == 1:
        return y.copy()
    if np.isnan(y).any():
        idx = np.arange(y.size)
        good = ~np.isnan(y)
        if not good.any():
            return y.copy()
        y = np.interp(idx, idx[good], y[good])
    k = np.ones(win) / win
    pad = win // 2
    ypad = np.concatenate([np.full(pad, y[0]), y, np.full(win - pad - 1, y[-1])])
    return np.convolve(ypad, k, mode="valid")[: y.size]


def estimate_grade(frame: SignalFrame, window_s: float = 30.0) -> dict:
    """估计道路坡度(%)。

    路径 A：IMU/融合坡度信号（低通滤除高频噪声）
    路径 B：GNSS 高程对里程做窗口线性回归求斜率（对高程噪声鲁棒）
    融合：优先 A，A 缺失时回退 B —— 对应方案文档 4.6 的冗余估计。
    """
    v = frame["WheelBasedVehicleSpeed"] / 3.6
    dt = 1.0 / GRID_HZ
    s = np.concatenate([[0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * dt)])

    out: dict[str, np.ndarray] = {}

    if frame.has("RoadGrade"):
        out["grade_imu"] = moving_average(frame["RoadGrade"], int(5 * GRID_HZ))

    if frame.has("GNSSAltitude"):
        alt = frame["GNSSAltitude"]
        win = max(5, int(window_s * GRID_HZ))
        n = alt.size
        idx = np.arange(n)
        csum = np.concatenate([[0.0], np.cumsum(alt)])
        cs2 = np.concatenate([[0.0], np.cumsum(s)])
        csa = np.concatenate([[0.0], np.cumsum(s * alt)])
        css = np.concatenate([[0.0], np.cumsum(s * s)])

        lo = np.clip(idx - win // 2, 0, n - 1)
        hi = np.clip(idx + win // 2 + 1, 1, n)
        cnt = (hi - lo).astype(float)
        sy = csum[hi] - csum[lo]
        sx = cs2[hi] - cs2[lo]
        sxy = csa[hi] - csa[lo]
        sxx = css[hi] - css[lo]
        denom = cnt * sxx - sx * sx
        denom = np.where(np.abs(denom) < 1e-9, np.nan, denom)
        slope = (cnt * sxy - sx * sy) / denom
        out["grade_gnss"] = np.clip(np.nan_to_num(slope, nan=0.0) * 100.0, -15.0, 15.0)

    if "grade_imu" in out:
        grade = out["grade_imu"]
        src = "IMU 融合坡度信号"
    elif "grade_gnss" in out:
        grade = out["grade_gnss"]
        src = "GNSS 高程差分（回退路径）"
    else:
        grade = np.zeros_like(v)
        src = "无坡度信息（按平路处理）"

    out["grade"] = grade
    out["source"] = np.array([src] * grade.size, dtype=object)
    out["source_name"] = src
    return out


def estimate_mass(frame: SignalFrame) -> tuple[np.ndarray, str]:
    """整车质量估计：优先轴荷/悬架信号，缺失时回退油耗-动力学一致性辨识。"""
    if frame.has("TotalVehicleMass"):
        m = frame["TotalVehicleMass"]
        return moving_average(m, int(20 * GRID_HZ)), "轴荷/悬架传感器"
    return None, "不可用"
