"""整车纵向动力学与能量转换模型（矢量化的纯函数）。

对应方案文档 4.4 节《能耗模型》。

本模块只做"给定行驶状态 -> 力 -> 功率 -> 能量"的物理换算，
不含任何数据读取逻辑，因此既被仿真器（生成真值）使用，
也被分析流水线（从报文反算能耗）使用 —— 两边的物理口径完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

# 常量
G = 9.80665            # 重力加速度 m/s^2
RHO_AIR = 1.225        # 空气密度 kg/m^3 (15degC, 海平面)
DIESEL_LHV_MJ_PER_KG = 42.8      # 柴油低热值 MJ/kg
DIESEL_DENSITY_KG_PER_L = 0.84   # 柴油密度 kg/L
# 1 L 柴油 = 0.84 kg * 42.8 MJ/kg = 35.952 MJ = 9.9867 kWh
DIESEL_KWH_PER_L = DIESEL_DENSITY_KG_PER_L * DIESEL_LHV_MJ_PER_KG / 3.6
KWH_PER_MJ = 1.0 / 3.6


@dataclass
class VehicleParams:
    """整车参数（生产环境按车型标定，此处为 6x4 重型牵引车典型值）。"""

    name: str = "6x4 重型牵引车(柴油)"
    curb_mass_kg: float = 9500.0          # 整备质量
    payload_kg: float = 15000.0           # 载重
    c_rr: float = 0.0075                  # 滚动阻力系数
    c_d: float = 0.60                     # 空气阻力系数
    frontal_area_m2: float = 9.0          # 迎风面积
    rot_inertia_delta: float = 0.06       # 旋转惯量等效系数 delta (m_eff = m*(1+delta))
    eta_drivetrain: float = 0.92          # 传动效率
    engine_rated_power_kw: float = 353.0  # 发动机额定功率
    engine_idle_power_kw: float = 5.5     # 怠速摩擦/泵气等效功率
    aux_power_kw: float = 1.5             # 行驶附件功率（发电机/风扇）
    bsfc_best_g_per_kwh: float = 195.0    # 最佳有效燃油消耗率
    bsfc_best_load: float = 0.60          # 最佳油耗率对应的负荷率
    bsfc_curvature: float = 0.45          # 万有特性曲线弯曲程度（按实车标定）
    # --- 纯电车型参数 ---
    eta_motor: float = 0.93               # 电机+控制器效率
    regen_efficiency: float = 0.65        # 制动能量回收效率
    regen_power_limit_kw: float = 250.0   # 回收功率上限
    ev_aux_power_kw: float = 2.0          # 电车附件（助力/低压）
    ev_hvac_power_kw: float = 3.0         # 空调/加热基础功率
    pack_voltage_v: float = 650.0         # 电池包额定电压
    battery_capacity_kwh: float = 350.0   # 电池容量

    # ------------------------------------------------------------------
    def mass_kg(self) -> float:
        return self.curb_mass_kg + self.payload_kg

    def effective_mass_kg(self) -> float:
        return self.mass_kg() * (1.0 + self.rot_inertia_delta)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvParams(VehicleParams):
    name: str = "6x4 纯电牵引车"
    engine_rated_power_kw: float = 400.0
    eta_drivetrain: float = 0.95


# ----------------------------------------------------------------------
# 阻力
# ----------------------------------------------------------------------
def grade_percent_to_sin(grade_pct: np.ndarray) -> np.ndarray:
    """坡度百分比 -> sin(theta)。"""
    return np.sin(np.arctan(np.asarray(grade_pct, dtype=float) / 100.0))


def resistance_forces(
    speed_mps: np.ndarray,
    grade_pct: np.ndarray,
    p: VehicleParams,
    wind_mps: np.ndarray | float = 0.0,
    c_rr_scale: np.ndarray | float = 1.0,
    mass_override: np.ndarray | float | None = None,
) -> dict[str, np.ndarray]:
    """计算空气阻力 / 滚动阻力 / 坡道阻力（N）。

    mass_override 允许用**估计质量**（而非铭牌参数）参与计算 ——
    真实场景下整车质量随时变化，必须由传感器或辨识得到。
    """
    v = np.asarray(speed_mps, dtype=float)
    m = np.asarray(mass_override, dtype=float) if mass_override is not None else p.mass_kg()
    sin_theta = grade_percent_to_sin(grade_pct)
    cos_theta = np.sqrt(np.clip(1.0 - sin_theta ** 2, 0.0, 1.0))

    f_aero = 0.5 * RHO_AIR * p.c_d * p.frontal_area_m2 * (v + wind_mps) ** 2
    f_roll = m * G * p.c_rr * np.asarray(c_rr_scale, dtype=float) * cos_theta * np.sign(v)
    f_grade = m * G * sin_theta
    return {"aero": f_aero, "roll": f_roll, "grade": f_grade}


def road_load_power(
    speed_mps: np.ndarray,
    accel_mps2: np.ndarray,
    grade_pct: np.ndarray,
    p: VehicleParams,
    wind_mps: np.ndarray | float = 0.0,
    c_rr_scale: np.ndarray | float = 1.0,
    mass_override: np.ndarray | float | None = None,
) -> dict[str, np.ndarray]:
    """车轮处各路功率（W），符号约定：驱动为正。

    返回键：inertia / roll / aero / grade / total
    """
    v = np.asarray(speed_mps, dtype=float)
    a = np.asarray(accel_mps2, dtype=float)
    f = resistance_forces(v, grade_pct, p, wind_mps, c_rr_scale, mass_override)
    m_eff = (np.asarray(mass_override, dtype=float) if mass_override is not None
             else p.mass_kg()) * (1.0 + p.rot_inertia_delta)
    p_inertia = m_eff * a * v
    p_roll = f["roll"] * v
    p_aero = f["aero"] * v
    p_grade = f["grade"] * v
    total = p_inertia + p_roll + p_aero + p_grade
    return {"inertia": p_inertia, "roll": p_roll, "aero": p_aero,
            "grade": p_grade, "total": total}


# ----------------------------------------------------------------------
# 发动机 / 燃油
# ----------------------------------------------------------------------
def bsfc_g_per_kwh(power_kw: np.ndarray, p: VehicleParams) -> np.ndarray:
    """有效燃油消耗率曲线 g/kWh（万有特性简化模型）。

    低负荷时 bsfc 迅速恶化 —— 这正是"部分负荷损失"的物理来源。
    """
    load = np.clip(np.asarray(power_kw, dtype=float) / p.engine_rated_power_kw, 1e-4, 1.5)
    penalty = 1.0 + p.bsfc_curvature * (load - p.bsfc_best_load) ** 2 / (p.bsfc_best_load ** 2)
    return p.bsfc_best_g_per_kwh * penalty


def fuel_rate_lph(engine_power_kw: np.ndarray, p: VehicleParams) -> np.ndarray:
    """发动机输出功率 -> 燃油率 L/h。"""
    kw = np.asarray(engine_power_kw, dtype=float)
    kg_per_h = kw * bsfc_g_per_kwh(kw, p) / 1000.0
    return kg_per_h / DIESEL_DENSITY_KG_PER_L


def engine_power_from_wheel(
    wheel_power_w: np.ndarray,
    p: VehicleParams,
    aux_on: np.ndarray | float = 1.0,
) -> np.ndarray:
    """车轮需求功率 -> 发动机输出功率（kW）。制动时发动机不输出牵引功率。"""
    pw = np.asarray(wheel_power_w, dtype=float)
    traction_kw = np.clip(pw, 0.0, None) / 1000.0 / p.eta_drivetrain
    base = p.engine_idle_power_kw + p.aux_power_kw * np.asarray(aux_on, dtype=float)
    return base + traction_kw


# ----------------------------------------------------------------------
# 电驱 / 电池
# ----------------------------------------------------------------------
def battery_power_kw(
    wheel_power_w: np.ndarray,
    p: VehicleParams,
    hvac_kw: np.ndarray | float = 0.0,
) -> np.ndarray:
    """车轮需求功率 -> 电池侧功率（kW）。放电为正，回收为负。"""
    pw = np.asarray(wheel_power_w, dtype=float)
    p_drive = np.clip(pw, 0.0, None) / 1000.0
    p_brake = np.clip(-pw, 0.0, None) / 1000.0

    p_motor_in = p_drive / (p.eta_drivetrain * p.eta_motor)
    regen_mech = np.minimum(p_brake * p.eta_drivetrain, p.regen_power_limit_kw)
    p_motor_out = -regen_mech * p.eta_motor

    aux = p.ev_aux_power_kw + np.asarray(hvac_kw, dtype=float)
    return p_motor_in + p_motor_out + aux


# ----------------------------------------------------------------------
# 便捷聚合
# ----------------------------------------------------------------------
def integrate(y: np.ndarray, dt: float) -> float:
    """梯形积分（兼容 numpy 1.x / 2.x）。"""
    y = np.asarray(y, dtype=float)
    if y.size < 2:
        return 0.0
    fn = getattr(np, "trapezoid", None) or np.trapz
    return float(fn(y, dx=dt))


def cumtrapz(y: np.ndarray, dt: float) -> np.ndarray:
    """累计梯形积分，长度与输入一致（首元素为 0）。"""
    y = np.asarray(y, dtype=float)
    out = np.zeros_like(y)
    if y.size > 1:
        out[1:] = np.cumsum(0.5 * (y[1:] + y[:-1]) * dt)
    return out


def fuel_liters_from_rate(rate_lph: np.ndarray, dt: float) -> float:
    return integrate(rate_lph, dt) / 3600.0
