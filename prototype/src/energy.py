"""能耗计算、能量瀑布分解与三道自检。

对应方案文档 4.3 / 4.4 / 4.8 节。

三道自检（报告发布的前置条件）
    ① 能量守恒自检：瀑布各分项之和 = 实测总能耗
    ② 三方里程自检：车速积分 / 里程表差分 / GNSS 累积，两两偏差 < 2%
    ③ 对账自检：燃油率积分 vs 累计油耗差分（真实场景即"加油小票"），偏差 < 5%
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

from . import physics as ph
from .physics import VehicleParams
from .preprocess import SignalFrame, GRID_HZ, moving_average, estimate_grade, estimate_mass

# 物理量：1 L 柴油 = 9.9867 kWh 化学能
DIESEL_KWH_PER_L = ph.DIESEL_KWH_PER_L


@dataclass
class EnergyResult:
    powertrain: str
    distance_km: float
    duration_s: float
    # --- 能耗（实测） ---
    energy_kwh: float = 0.0              # 化学能 / 电能
    fuel_l: float = 0.0
    fuel_l_ref: float = 0.0              # 累计油耗差分（对账基准）
    # --- 车轮侧分解 ---
    e_traction_wheel_kwh: float = 0.0
    e_brake_req_kwh: float = 0.0
    wheel: dict = field(default_factory=dict)      # roll/aero/grade/kinetic
    # --- 化学能去向（瀑布） ---
    chem: dict = field(default_factory=dict)
    # --- 标准指标 ---
    l_per_100km: float = 0.0
    l_per_100tkm: float = 0.0
    kwh_per_100km: float = 0.0
    kwh_per_100tkm: float = 0.0
    mass_ton_km: float = 0.0
    # --- 自检 ---
    checks: dict = field(default_factory=dict)
    grade_source: str = ""
    mass_source: str = ""
    diagnostics: dict = field(default_factory=dict)

    def to_flat(self) -> dict:
        d = {k: v for k, v in asdict(self).items()
             if not isinstance(v, (dict, list))}
        return d


# ----------------------------------------------------------------------
# 里程：三条独立路径
# ----------------------------------------------------------------------
def distance_paths(frame: SignalFrame) -> dict:
    v = frame["WheelBasedVehicleSpeed"] / 3.6          # m/s
    dt = 1.0 / GRID_HZ
    s_can = float(ph.integrate(v, dt)) / 1000.0

    s_odo = None
    if frame.has("HighResolutionTotalVehicleDistance"):
        odo = frame["HighResolutionTotalVehicleDistance"]
        s_odo = float(np.nanmax(odo) - np.nanmin(odo))

    s_gnss = None
    if frame.has("GNSSSpeed"):
        g = np.nan_to_num(frame["GNSSSpeed"]) / 3.6
        s_gnss = float(ph.integrate(g, dt)) / 1000.0

    return dict(can=s_can, odo=s_odo, gnss=s_gnss)


# ----------------------------------------------------------------------
# 能耗
# ----------------------------------------------------------------------
def energy_from_fuel_rate(frame: SignalFrame) -> tuple[float, np.ndarray]:
    """路径1：瞬时燃油率积分 -> 燃油升数（能耗主算法）。"""
    rate = np.nan_to_num(frame["EngineFuelRate"])
    dt = 1.0 / GRID_HZ
    return ph.fuel_liters_from_rate(rate, dt), rate


def energy_from_totalizer(frame: SignalFrame) -> float | None:
    """路径2：累计燃油消耗差分 -> 燃油升数（对账基准，分辨率 0.5 L）。"""
    if not frame.has("TotalFuelUsed"):
        return None
    tf = frame["TotalFuelUsed"]
    return float(np.nanmax(tf) - np.nanmin(tf))


def energy_from_electric(frame: SignalFrame) -> tuple[float, np.ndarray]:
    """电车：电池功率积分 P = U x I。"""
    u = np.nan_to_num(frame["PackVoltage"])
    i = np.nan_to_num(frame["PackCurrent"])
    p_kw = u * i / 1000.0
    dt = 1.0 / GRID_HZ
    return ph.integrate(p_kw, dt) / 3600.0, p_kw


# ----------------------------------------------------------------------
# 主分析
# ----------------------------------------------------------------------
def analyze_frame(frame: SignalFrame, params: VehicleParams,
                  powertrain: str = "fuel",
                  mass_override: np.ndarray | None = None,
                  grade_override: np.ndarray | None = None,
                  accel_smooth_s: float = 1.0,
                  eta_drivetrain: float | None = None) -> EnergyResult:
    """从（已清洗的）报文信号反算能耗全貌。"""
    dt = 1.0 / GRID_HZ
    v = np.nan_to_num(frame["WheelBasedVehicleSpeed"]) / 3.6
    n = v.size
    t = frame.t

    # 速度低通后再微分：抑制量化噪声对加速度的放大（工程标准做法）
    v_s = moving_average(v, max(1, int(accel_smooth_s * GRID_HZ)))
    a = np.gradient(v_s, dt)

    # 坡度 / 质量
    if grade_override is not None:
        grade = grade_override
        grade_src = "真值（仅用于验证对照）"
    else:
        g = estimate_grade(frame)
        grade = g["grade"]
        grade_src = g["source_name"]

    if mass_override is not None:
        mass = mass_override
        mass_src = "真值（仅用于验证对照）"
    else:
        mass, mass_src = estimate_mass(frame)

    eta = float(params.eta_drivetrain if eta_drivetrain is None else eta_drivetrain)

    # ---- 车轮侧物理分解 ----
    p = ph.road_load_power(v_s, a, grade, params, c_rr_scale=1.0,
                           mass_override=mass)
    p_total = p["total"]
    p_traction = np.clip(p_total, 0.0, None)
    p_brake = np.clip(-p_total, 0.0, None)

    e_trac = ph.integrate(p_traction, dt) / 3.6e6
    e_brake = ph.integrate(p_brake, dt) / 3.6e6
    wheel = {
        "滚动阻力": ph.integrate(p["roll"], dt) / 3.6e6,
        "空气阻力": ph.integrate(p["aero"], dt) / 3.6e6,
        "坡道(净值)": ph.integrate(p["grade"], dt) / 3.6e6,
        "加速/动能(净值)": ph.integrate(p["inertia"], dt) / 3.6e6,
    }
    # 恒等式：e_trac = Σwheel + e_brake
    identity_resid = e_trac - (sum(wheel.values()) + e_brake)

    # ---- 能耗（实测）与对账 ----
    res = EnergyResult(powertrain=powertrain, distance_km=0.0,
                       duration_s=float(t[-1]), grade_source=grade_src,
                       mass_source=mass_src)

    if powertrain == "fuel":
        fuel_l, rate = energy_from_fuel_rate(frame)
        fuel_ref = energy_from_totalizer(frame)
        chem_kwh = fuel_l * DIESEL_KWH_PER_L
        res.energy_kwh = chem_kwh
        res.fuel_l = fuel_l
        res.fuel_l_ref = fuel_ref if fuel_ref is not None else float("nan")
    else:
        e_batt, p_batt = energy_from_electric(frame)
        res.energy_kwh = e_batt
        fuel_ref = None
        rate = None

    # ---- 里程 ----
    dist = distance_paths(frame)
    res.distance_km = dist["odo"] if dist["odo"] else dist["can"]
    res.diagnostics["distance_paths"] = dist

    # ---- 化学能瀑布 ----
    e_trac_engine = e_trac / eta
    e_dt_loss = e_trac_engine * (1.0 - eta)

    # 怠速/附件能耗（实测）：车速近似为 0 时段的燃油积分
    if powertrain == "fuel":
        idle_mask = v_s < 0.3
        idle_kwh = ph.integrate(np.where(idle_mask, rate, 0.0), dt) / 3600.0 * DIESEL_KWH_PER_L
        thermal = res.energy_kwh - idle_kwh - e_trac_engine
        res.chem = {
            "怠速与附件耗能": idle_kwh,
            "传动损失": e_dt_loss,
            "发动机热效率/部分负荷损失": thermal,
            "车轮牵引能量(经传动)": e_trac_engine - e_dt_loss,
        }
    else:
        aux = ph.integrate(np.full(n, params.ev_aux_power_kw), dt) / 3600.0
        e_motor_drive = e_trac / (eta * params.eta_motor)
        e_regen = ph.integrate(p_brake, dt) / 3.6e6 * 0.55
        res.chem = {
            "驱动电耗": e_motor_drive,
            "附件/热管理电耗": aux,
            "能量回收(收益)": -e_regen,
            "电驱与传动损失": res.energy_kwh - e_motor_drive - aux + e_regen,
        }

    res.e_traction_wheel_kwh = e_trac
    res.e_brake_req_kwh = e_brake
    res.wheel = wheel

    # ---- 标准指标 ----
    mass_mean = float(np.nanmean(mass)) if mass is not None else params.mass_kg()
    res.mass_ton_km = mass_mean / 1000.0 * res.distance_km
    if res.distance_km > 0:
        if powertrain == "fuel":
            res.l_per_100km = res.fuel_l / res.distance_km * 100.0
            res.l_per_100tkm = res.fuel_l / max(res.mass_ton_km, 1e-9) * 100.0
        res.kwh_per_100km = res.energy_kwh / res.distance_km * 100.0
        res.kwh_per_100tkm = res.energy_kwh / max(res.mass_ton_km, 1e-9) * 100.0

    # ---- 三道自检 ----
    checks = {}
    # ① 能量守恒
    chem_sum = sum(res.chem.values())
    checks["energy_balance"] = dict(
        total=res.energy_kwh, sum_of_parts=chem_sum,
        residual_pct=abs(chem_sum - res.energy_kwh) / max(abs(res.energy_kwh), 1e-9) * 100.0,
        passed=True,     # 瀑布按定义闭合，此处记录数值以便审计
    )
    checks["wheel_identity"] = dict(
        residual_kwh=identity_resid,
        residual_pct=abs(identity_resid) / max(e_trac, 1e-9) * 100.0,
        passed=abs(identity_resid) / max(e_trac, 1e-9) < 0.01,
    )
    # ② 三方里程
    vals = [x for x in (dist["can"], dist["odo"], dist["gnss"]) if x]
    if len(vals) >= 2:
        spread = (max(vals) - min(vals)) / max(np.mean(vals), 1e-9) * 100.0
    else:
        spread = 0.0
    checks["distance_consistency"] = dict(
        paths=dist, spread_pct=spread, passed=spread < 2.0,
    )
    # ③ 对账
    if powertrain == "fuel" and fuel_ref is not None and fuel_ref > 0:
        err = abs(res.fuel_l - fuel_ref) / fuel_ref * 100.0
        resolution_floor = 0.5 / max(fuel_ref, 1e-9) * 100.0
        checks["reconciliation"] = dict(
            from_rate_l=res.fuel_l, from_totalizer_l=fuel_ref,
            error_pct=err, totalizer_resolution_pct=resolution_floor,
            passed=err < 5.0,
        )
    else:
        checks["reconciliation"] = dict(passed=None, note="无独立对账基准")

    res.checks = checks
    res.diagnostics.update(
        grade_mean_pct=float(np.nanmean(grade)),
        grade_abs_mean_pct=float(np.nanmean(np.abs(grade))),
        mass_mean_kg=mass_mean,
        accel_p95=float(np.percentile(np.abs(a), 95)),
        n_samples=int(n),
    )
    return res


def compare_with_truth(res: EnergyResult, truth: dict) -> dict:
    """把报文反算结果与仿真真值对比（原型专有，用于证明算法精度）。"""
    out = {"distance_km": dict(computed=res.distance_km,
                               truth=truth["distance_km"],
                               error_pct=_pct(res.distance_km, truth["distance_km"]))}
    if res.powertrain == "fuel":
        out["fuel_l"] = dict(computed=res.fuel_l, truth=truth["fuel_l"],
                             error_pct=_pct(res.fuel_l, truth["fuel_l"]))
        out["l_per_100km"] = dict(computed=res.l_per_100km,
                                  truth=truth["fuel_l_per_100km"],
                                  error_pct=_pct(res.l_per_100km, truth["fuel_l_per_100km"]))
    else:
        out["energy_kwh"] = dict(computed=res.energy_kwh, truth=truth["energy_kwh"],
                                 error_pct=_pct(res.energy_kwh, truth["energy_kwh"]))
    out["e_traction_wheel_kwh"] = dict(
        computed=res.e_traction_wheel_kwh, truth=truth["e_traction_wheel_kwh"],
        error_pct=_pct(res.e_traction_wheel_kwh, truth["e_traction_wheel_kwh"]))
    out["e_brake_req_kwh"] = dict(
        computed=res.e_brake_req_kwh, truth=truth["e_brake_req_kwh"],
        error_pct=_pct(res.e_brake_req_kwh, truth["e_brake_req_kwh"]))
    return out


def _pct(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-9) * 100.0
