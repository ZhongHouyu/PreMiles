"""行程仿真器 —— 生成带"真值"的行程数据与整车 CAN 报文日志。

为什么先做仿真（方案文档 9 节）：
真实报文受保密与申请周期限制，而仿真数据**自带真值**，
可以先证明"算法算得准"；拿到真车数据后只需替换数据源，流水线不变。

真值链路（全部矢量化，秒级生成上千公里数据）：
    行驶工况(速度/坡度) -> 纵向动力学 -> 车轮功率 -> 发动机/电机 -> 燃油率/电池功率 -> 能耗
再叠加真实的测量缺陷：量化、噪声、丢帧、突发丢包、GNSS 高程噪声。
"""

from __future__ import annotations

import copy
import zlib
from dataclasses import dataclass, field

import numpy as np

from . import physics as ph
from .physics import VehicleParams, EvParams

DT = 0.05          # 真值积分步长 20 Hz
LOG_HZ = 10.0      # 报文落地频率（T-Box 上传典型值）


def stable_seed(*parts: object) -> int:
    """确定性随机种子。

    绝不能用内置 hash() —— 它对字符串默认按进程随机加盐，
    会导致同一个 trip_id 每次运行生成完全不同的行程，结果完全不可复现。
    """
    return zlib.crc32("|".join(map(str, parts)).encode("utf-8")) % (2 ** 31)


# ======================================================================
# 工况构造
# ======================================================================
def _segment_array(kind: str, dur_s: float, v0_kph: float, v1_kph: float) -> np.ndarray:
    n = max(2, int(round(dur_s / DT)))
    if kind == "ramp":
        return np.linspace(v0_kph, v1_kph, n, endpoint=False) / 3.6
    return np.full(n, v0_kph / 3.6)


def build_speed_profile(style: float = 1.0, rng: np.random.Generator | None = None,
                        idle_extra_s: float = 0.0,
                        power_cap_w: float = 3.0e5,
                        m_eff_kg: float = 25000.0,
                        speed_scale: float = 1.0) -> np.ndarray:
    """构造一段 30 分钟级的多工况行驶速度曲线。

    style       : 驾驶激烈度 —— 影响急加速/急减速事件的**数量与幅度**。
    speed_scale : 目标车速缩放 —— 激烈驾驶在卡车上的主要能耗代价其实是**超速**
                  （空气阻力正比于 v³），而不是更大的加速度：
                  353 kW 的卡车本来也做不出比例更高的加速度。

    末尾按惯性项做一次功率封顶；坡度相关的精确封顶由
    `cap_by_available_power` 在已知坡道后再做一遍。
    """
    rng = rng or np.random.default_rng(0)
    s = max(0.6, float(style))
    ss = max(0.5, float(speed_scale))
    # 激烈度对"坡道时长"的影响要做压缩：
    # 若直接按 style 成比例缩短坡道，加速度需求会超过发动机可用功率，
    # 功率封顶后车辆永远追不上目标车速，行程结构被破坏（里程腰斩）。
    rs = 1.0 + (s - 1.0) * 0.30
    segs: list[tuple[str, float, float, float]] = []

    segs.append(("const", 60, 0, 0))                       # 起步前怠速

    # 城市走走停停 4 个循环
    for _ in range(4):
        segs += [("ramp", 26 / rs, 0, 30), ("const", 42, 30, 30),
                 ("ramp", 21 / rs, 30, 0), ("const", 14, 0, 0)]

    # 城郊中速
    segs += [("ramp", 38 / rs, 0, 50), ("const", 300, 50, 50),
             ("ramp", 30 / rs, 50, 0), ("const", 26, 0, 0)]

    # 高速巡航（干线运输主工况）
    segs += [("ramp", 58 / rs, 0, 80), ("const", 900, 80, 80),
             ("ramp", 36 / rs, 80, 40), ("const", 150, 40, 40),
             ("ramp", 30 / rs, 40, 0), ("const", 22, 0, 0)]

    # 末端市区
    segs += [("ramp", 24 / rs, 0, 35), ("const", 120, 35, 35),
             ("ramp", 20 / rs, 35, 0), ("const", 30, 0, 0)]

    # 额外怠速（异常注入：长时间怠速不熄火）
    if idle_extra_s > 0:
        segs.append(("const", float(idle_extra_s), 0, 0))

    # 目标车速缩放（仅作用于非零目标车速，怠速段保持为 0）
    segs = [(k, d, v0 * ss if v0 > 0 else 0.0, v1 * ss if v1 > 0 else 0.0)
            for (k, d, v0, v1) in segs]

    v = np.concatenate([_segment_array(*seg) for seg in segs])

    # 驾驶员抖动：加速度白噪声经低通后积分，再高通去漂移
    sigma_a = 0.03 * s
    noise = rng.normal(0.0, sigma_a, v.size)
    k = np.ones(60) / 60.0                                   # 3 s 低通
    noise = np.convolve(noise, k, mode="same")
    pert = np.cumsum(noise) * DT
    k2 = np.ones(1200) / 1200.0                              # 60 s 高通
    pert = pert - np.convolve(pert, k2, mode="same")
    pert = np.clip(pert, -0.5, 0.5)
    v = np.clip(v + pert, 0.0, None)

    # 急加速 / 急减速事件：成对高斯加速度脉冲，净冲量≈0（速度回到原轨迹）。
    # 关键：脉冲幅度必须先按"该时刻可用功率"缩放 ——
    # 否则加速半程被功率封顶、减速半程不受限，速度会逐次向下棘轮，
    # 一趟下来平均车速被凭空压低（行程里程腰斩）。
    a_feas = power_cap_w / (m_eff_kg * np.clip(v, 2.0, None))
    n_events = int(round(rng.uniform(2, 5) * (s ** 1.8)))
    if n_events > 0:
        t = np.arange(v.size) * DT
        extra = np.zeros_like(v)
        span = max(120.0, (t[-1] - 200.0) / max(1, n_events))
        for i in range(n_events):
            t0 = 100.0 + i * span + rng.uniform(0, span * 0.35)
            hard_acc = bool(rng.random() < 0.5)
            amp_des = (1.30 if hard_acc else -1.60) * (0.85 + 0.3 * rng.random()) * min(s, 1.9)
            lim = max(float(a_feas[min(int(t0 / DT), a_feas.size - 1)]) * 0.8, 0.15)
            amp = float(np.sign(amp_des)) * min(abs(amp_des), lim)
            w = 2.2
            g = np.exp(-((t - t0) / w) ** 2)
            g2 = np.exp(-((t - t0 - 16.0) / w) ** 2)
            extra += amp * (g - g2)
        v = np.clip(v + np.cumsum(extra) * DT, 0.0, None)

    # 惯性项功率封顶（坡度未知时的第一遍保险）
    a = np.gradient(v, DT)
    a_cap = power_cap_w / (m_eff_kg * np.clip(v, 2.0, None))
    a = np.minimum(a, np.maximum(a_cap, 0.05))
    a = np.maximum(a, -2.2)
    return np.clip(v[0] + np.cumsum(a) * DT, 0.0, None)


def build_grade_profile(distance_m: np.ndarray, seed: int = 0) -> np.ndarray:
    """按里程定义道路坡度(%)：起伏丘陵 + 一处长上坡 + 一处长下坡。"""
    s = np.asarray(distance_m, dtype=float)
    g = (2.2 * np.sin(2 * np.pi * s / 9000.0 + seed * 0.7)
         + 1.4 * np.sin(2 * np.pi * s / 3700.0 + 1.1 + seed * 0.3)
         + 0.8 * np.sin(2 * np.pi * s / 1500.0 + 2.0))
    g += 3.6 * np.exp(-((s - 0.55 * s.max()) / 900.0) ** 2)
    g -= 3.2 * np.exp(-((s - 0.72 * s.max()) / 1050.0) ** 2)
    return np.clip(g, -8.0, 8.0)


# ======================================================================
# 行程规格与真值
# ======================================================================
@dataclass
class TripSpec:
    trip_id: str
    day: int
    payload_kg: float = 15000.0
    style: float = 1.0            # 驾驶激烈度
    c_rr_scale: float = 1.0       # 滚阻恶化倍数（胎压不足/刹车拖滞）
    bsfc_scale: float = 1.0       # 燃烧效率恶化
    idle_extra_s: float = 0.0     # 额外怠速时长
    ambient_c: float = 20.0
    powertrain: str = "fuel"      # fuel | ev
    route_seed: int = 0
    speed_scale: float = 1.0      # 目标车速缩放（超速倾向）
    injected_fault: str = "normal"
    log_can: bool = False


@dataclass
class Trip:
    """行程真值 + 可选的原始报文日志路径。"""

    spec: TripSpec
    t: np.ndarray
    v: np.ndarray            # m/s
    a: np.ndarray            # m/s^2
    s: np.ndarray            # m
    grade: np.ndarray        # %
    mass: np.ndarray         # kg
    p_roll: np.ndarray       # W
    p_aero: np.ndarray
    p_grade: np.ndarray
    p_inertia: np.ndarray
    p_total: np.ndarray
    p_traction: np.ndarray
    p_brake: np.ndarray
    energy_rate_kw: np.ndarray   # 燃油车=燃油化学能 kW；电车=电池功率 kW
    fuel_rate_lph: np.ndarray | None = None
    battery_kw: np.ndarray | None = None
    soc: np.ndarray | None = None
    can_log: str | None = None
    truth: dict = field(default_factory=dict)

    @property
    def dt(self) -> float:
        return DT


def _gear_ratio_from_speed(v_mps: np.ndarray) -> np.ndarray:
    """由车速反推挡位与发动机转速（稳态换挡策略，矢量化）。"""
    ratios = np.array([14.0, 11.0, 8.6, 6.7, 5.3, 4.2, 3.4, 2.85])
    wheel_rpm = np.asarray(v_mps, dtype=float) * 18.35     # 滚动半径 0.52 m
    rpm = wheel_rpm[:, None] * ratios[None, :]
    ok = rpm >= 1050.0
    # 选满足转速要求的最高挡（ratio 最小）
    idx = np.where(ok.any(axis=1), ok.shape[1] - 1 - np.argmax(ok[:, ::-1], axis=1), 0)
    eng_rpm = rpm[np.arange(v_mps.size), idx]
    return np.where(v_mps < 1.0, 600.0, np.clip(eng_rpm, 600.0, 2100.0))


def _smooth(y: np.ndarray, win: int) -> np.ndarray:
    """滑动平均（边界复制填充），用于抑制差分求加速度时的折角伪峰。"""
    win = max(1, int(win))
    if win <= 1:
        return np.asarray(y, dtype=float)
    k = np.ones(win) / win
    pad = win // 2
    yp = np.concatenate([np.full(pad, y[0]), y, np.full(win - pad - 1, y[-1])])
    return np.convolve(yp, k, mode="valid")[: y.size]


def solve_feasible_profile(v_target: np.ndarray, grade: np.ndarray,
                           params: VehicleParams, power_cap_w: float,
                           tau_s: float = 6.0) -> np.ndarray:
    """在**可用功率约束**下求解真实可达的速度轨迹（闭环驾驶员模型）。

    两个必须同时成立的物理事实：
      1. 功率不足时车辆必须减速 —— 满载爬长坡时"维持当前车速"所需的功率本身
         就可能超过额定功率（这里不加 a>=0 的下限，p_avail<0 时 a_cap 自动为负）。
      2. 坡道过去后车辆会重新加速回到目标车速 —— 若只做单向限幅，
         车辆会永远滞后于目标曲线，一趟下来里程腰斩。

    驾驶员模型：一阶跟随 `a_des = (v_target - v)/tau`，再受功率约束裁剪。
    这是**顺序递推**（每一步依赖上一步车速），因此用显式循环而不是向量化迭代 ——
    向量化迭代会因加速度偏置在积分中累积而发散。
    """
    m_eff = params.effective_mass_kg()
    m = params.mass_kg()
    sin_t = ph.grade_percent_to_sin(grade)
    cos_t = np.sqrt(np.clip(1.0 - sin_t ** 2, 0.0, 1.0))
    f_roll = (m * ph.G * params.c_rr * cos_t).tolist()      # 需再乘 sign(v)
    f_grade = (m * ph.G * sin_t).tolist()
    aero_c = 0.5 * ph.RHO_AIR * params.c_d * params.frontal_area_m2
    vt = np.asarray(v_target, dtype=float).tolist()

    n = len(vt)
    v = [0.0] * n
    v[0] = vt[0]
    inv_tau = 1.0 / max(tau_s, 0.1)
    for i in range(1, n):
        vi = v[i - 1]
        ti = vt[i]
        a_des = (ti - vi) * inv_tau
        if a_des > 2.0:
            a_des = 2.0
        elif a_des < -2.2:
            a_des = -2.2
        p_res = (f_roll[i] * (1.0 if vi > 0.0 else 0.0)
                 + aero_c * vi * vi + f_grade[i]) * vi
        a_cap = (power_cap_w - p_res) / (m_eff * (vi if vi > 2.0 else 2.0))
        a = a_des if a_des < a_cap else a_cap
        if a < -2.2:
            a = -2.2
        nv = vi + a * DT
        v[i] = nv if nv > 0.0 else 0.0
    return np.asarray(v)


def simulate_trip(spec: TripSpec, params: VehicleParams | None = None) -> Trip:
    """生成一段行程的全部真值。"""
    if params is None:
        params = EvParams() if spec.powertrain == "ev" else VehicleParams()
    else:
        params = copy.copy(params)
    # 载荷是能耗第一敏感因子，必须真正进入物理模型
    params.payload_kg = float(spec.payload_kg)
    if spec.powertrain == "ev":
        params.eta_drivetrain = max(params.eta_drivetrain, 0.95)

    rng = np.random.default_rng(stable_seed(spec.trip_id))
    m_eff = params.effective_mass_kg()
    # 车轮侧可用功率上限（额定功率扣除传动损失与附件并留少量余量）
    p_wheel_cap = params.engine_rated_power_kw * 1000.0 * 0.88

    v_target = build_speed_profile(style=spec.style, rng=rng,
                                   idle_extra_s=spec.idle_extra_s,
                                   power_cap_w=p_wheel_cap, m_eff_kg=m_eff,
                                   speed_scale=spec.speed_scale)
    # 坡度是里程的函数：先用目标曲线求一次坡度，再解算功率可达轨迹。
    # 由于实际里程与目标里程略有差异，坡道位置会平移，需要再迭代一次
    # 让"控制器所依据的坡度"与"真值所用的坡度"一致，否则功率约束会落错位置。
    s_target = np.concatenate([[0.0], np.cumsum(0.5 * (v_target[1:] + v_target[:-1]) * DT)])
    grade_plan = build_grade_profile(s_target, seed=spec.route_seed)
    v = solve_feasible_profile(v_target, grade_plan, params, p_wheel_cap)
    for _ in range(2):
        s_plan = np.concatenate([[0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * DT)])
        grade_plan = build_grade_profile(s_plan, seed=spec.route_seed)
        v = solve_feasible_profile(v_target, grade_plan, params, p_wheel_cap)

    n = v.size
    t = np.arange(n) * DT
    # 对差分加速度做 0.2 s 平滑：折角处中心差分会产生虚假尖峰，
    # 使瞬时轮上功率瞬时超过额定值（实测约占 2%~5% 采样点）。
    a = _smooth(np.gradient(v, DT), 4)
    s = np.concatenate([[0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * DT)])
    grade = build_grade_profile(s, seed=spec.route_seed)

    mass = np.full(n, params.curb_mass_kg + spec.payload_kg)
    p = ph.road_load_power(v, a, grade, params, c_rr_scale=spec.c_rr_scale)

    p_traction = np.clip(p["total"], 0.0, None)
    p_brake = np.clip(-p["total"], 0.0, None)

    if spec.powertrain == "fuel":
        eng_kw = ph.engine_power_from_wheel(p["total"], params)
        rate = ph.fuel_rate_lph(eng_kw, params) * spec.bsfc_scale
        energy_rate_kw = rate * ph.DIESEL_KWH_PER_L
        fuel_rate = rate
        battery = None
        soc = None
    else:
        hvac = np.where(spec.ambient_c < 10, params.ev_hvac_power_kw * 1.6,
                        np.where(spec.ambient_c > 28, params.ev_hvac_power_kw * 1.3, 0.0))
        battery = ph.battery_power_kw(p["total"], params, hvac_kw=hvac)
        energy_rate_kw = battery
        fuel_rate = None
        e_used = np.concatenate([[0.0], np.cumsum(battery * DT / 3600.0)])
        soc = np.clip(80.0 - e_used / params.battery_capacity_kwh * 100.0, 5.0, 100.0)

    trip = Trip(
        spec=spec, t=t, v=v, a=a, s=s, grade=grade, mass=mass,
        p_roll=p["roll"], p_aero=p["aero"], p_grade=p["grade"],
        p_inertia=p["inertia"], p_total=p["total"],
        p_traction=p_traction, p_brake=p_brake,
        energy_rate_kw=energy_rate_kw, fuel_rate_lph=fuel_rate,
        battery_kw=battery, soc=soc,
    )
    trip.truth = compute_truth(trip, params)
    return trip


def compute_truth(trip: Trip, params: VehicleParams) -> dict:
    """行程级真值指标（仿真器"上帝视角"，用于校验算法）。"""
    dt = DT
    dist_km = float(trip.s[-1] / 1000.0)
    e_brake = ph.integrate(trip.p_brake, dt) / 3.6e6          # kWh
    e_traction = ph.integrate(trip.p_traction, dt) / 3.6e6    # kWh
    e_roll = ph.integrate(trip.p_roll, dt) / 3.6e6
    e_aero = ph.integrate(trip.p_aero, dt) / 3.6e6
    e_grade = ph.integrate(trip.p_grade, dt) / 3.6e6
    e_inertia = ph.integrate(trip.p_inertia, dt) / 3.6e6
    e_energy = ph.integrate(trip.energy_rate_kw, dt) / 3600.0  # kWh

    out = dict(
        distance_km=dist_km,
        duration_s=float(trip.t[-1]),
        energy_kwh=e_energy,
        e_traction_wheel_kwh=e_traction,
        e_brake_req_kwh=e_brake,
        e_roll_kwh=e_roll,
        e_aero_kwh=e_aero,
        e_grade_kwh=e_grade,
        e_inertia_kwh=e_inertia,
        avg_speed_kph=dist_km / (trip.t[-1] / 3600.0),
        payload_kg=trip.spec.payload_kg,
        gross_mass_kg=params.curb_mass_kg + trip.spec.payload_kg,
        mass_ton_km=(params.curb_mass_kg + trip.spec.payload_kg) / 1000.0 * dist_km,
    )
    if trip.spec.powertrain == "fuel":
        fuel_l = e_energy / ph.DIESEL_KWH_PER_L
        out.update(
            fuel_l=fuel_l,
            fuel_l_per_100km=fuel_l / dist_km * 100.0,
            fuel_l_per_100tkm=fuel_l / out["mass_ton_km"] * 100.0,
        )
    else:
        out.update(
            energy_kwh_per_100km=e_energy / dist_km * 100.0,
            energy_kwh_per_100tkm=e_energy / out["mass_ton_km"] * 100.0,
        )
    return out


# ======================================================================
# 报文落地
# ======================================================================
def materialize_can(trip: Trip, out_path: str, params: VehicleParams,
                    drop_rate: float = 0.002, burst_drops: int = 3) -> str:
    """把行程真值编码成整车 CAN 报文并写出 candump 日志。

    叠加真实测量缺陷：信号量化、传感器噪声、随机丢帧、突发丢包。
    """
    from . import can_io
    from .dbc_spec import MESSAGES
    from pathlib import Path

    db = can_io.load_dbc(Path(__file__).resolve().parent.parent / "config" / "trucks_demo.dbc")
    rng = np.random.default_rng(stable_seed(trip.spec.trip_id, "can"))
    is_ev = trip.spec.powertrain == "ev"

    # 生命周期初值（真实车辆上的累计量不会是 0）
    fuel0 = float(rng.uniform(20000, 90000))
    dist0 = float(rng.uniform(80000, 400000))

    eng_rpm = _gear_ratio_from_speed(trip.v)
    if is_ev:
        load_pct = np.zeros_like(trip.v)
    else:
        load_pct = np.clip(
            ph.engine_power_from_wheel(trip.p_total, params)
            / params.engine_rated_power_kw * 100.0, 0.0, 125.0)

    if not is_ev:
        fuel_cum = np.concatenate([[0.0], np.cumsum(trip.fuel_rate_lph * DT / 3600.0)]) + fuel0
    dist_cum = trip.s / 1000.0 + dist0
    grade_meas = trip.grade + rng.normal(0, 0.35, trip.v.size)          # IMU 坡度噪声
    alt_true = ph.cumtrapz(trip.grade / 100.0 * np.gradient(trip.s), DT)
    alt = alt_true + rng.normal(0, 0.9, trip.v.size)                    # GNSS 高程噪声
    gnss_speed = trip.v * 3.6 + rng.normal(0, 0.35, trip.v.size)
    mass_meas = trip.mass + rng.normal(0, 180, trip.v.size)             # 悬架压力抖动
    heading = np.mod(np.cumsum(trip.v * DT / 6371000.0 * 180 / np.pi * 0.15), 360.0)
    lat = 30.0 + np.cumsum(trip.v * DT * np.cos(np.radians(heading)) / 111320.0)
    lon = 114.0 + np.cumsum(trip.v * DT * np.sin(np.radians(heading)) / (111320.0 * 0.86))

    n_log = int(trip.t[-1] * LOG_HZ) + 1
    t_log = np.arange(n_log) / LOG_HZ
    idx = np.clip((t_log / DT).astype(int), 0, trip.v.size - 1)

    def sub(arr) -> np.ndarray:
        return np.asarray(arr, dtype=float)[idx]

    ts_all: list[np.ndarray] = []
    ids_all: list[np.ndarray] = []
    dat_all: list[np.ndarray] = []
    t_base = 1_700_000_000.0 + trip.spec.day * 86400.0

    for m in MESSAGES:
        if m["name"] == "TRK_BMS1" and not is_ev:
            continue
        step = max(1, int(round(LOG_HZ / m["rate_hz"])))
        sel = np.arange(0, n_log, step)

        if m["name"] == "EEC1":
            vals = {"EngineSpeed": sub(eng_rpm)[sel],
                    "ActualEnginePercentTorque": sub(load_pct)[sel]}
        elif m["name"] == "CCVS":
            brake = (sub(trip.p_brake) > 500.0).astype(float)
            vals = {"WheelBasedVehicleSpeed":
                    np.rint(sub(trip.v)[sel] * 3.6 * 256) / 256,
                    "BrakeSwitch": brake[sel]}
        elif m["name"] == "LFE":
            fr = sub(trip.fuel_rate_lph)[sel]
            fr = np.rint((fr + rng.normal(0, 0.25, fr.size)) / 0.05) * 0.05
            vals = {"EngineFuelRate": np.clip(fr, 0, None),
                    "TotalFuelUsed": np.rint(sub(fuel_cum)[sel] / 0.5) * 0.5}
        elif m["name"] == "VDHR":
            vals = {"HighResolutionTotalVehicleDistance":
                    np.rint(sub(dist_cum)[sel] / 0.005) * 0.005}
        elif m["name"] == "AMB":
            vals = {"AmbientAirTemperature":
                    np.rint((np.full(sel.size, trip.spec.ambient_c)
                             + rng.normal(0, 0.4, sel.size)) / 0.03125) * 0.03125}
        elif m["name"] == "TRK_LOAD1":
            vals = {"TotalVehicleMass": np.rint(sub(mass_meas)[sel]),
                    "AxleLoadFront": np.rint(sub(mass_meas)[sel] * 0.30)}
        elif m["name"] == "TRK_ENV1":
            vals = {"RoadGrade": np.rint(sub(grade_meas)[sel] / 0.001) * 0.001,
                    "WindSpeed": np.zeros(sel.size)}
        elif m["name"] == "TRK_TPMS1":
            # 滚阻恶化（胎压不足/刹车拖滞）会同时反映为轮胎压力下降
            tp = 830.0 if trip.spec.c_rr_scale < 1.2 else 830.0 / trip.spec.c_rr_scale
            tp = tp + rng.normal(0, 8.0, sel.size)          # 传感器噪声
            vals = {"TirePressureFront": np.rint(tp / 5.0) * 5.0,
                    "TirePressureRear": np.rint((tp - 15.0) / 5.0) * 5.0}
        elif m["name"] == "TRK_GNSS1":
            vals = {"Latitude": np.rint(sub(lat)[sel] / 1e-7) * 1e-7,
                    "Longitude": np.rint(sub(lon)[sel] / 1e-7) * 1e-7}
        elif m["name"] == "TRK_GNSS2":
            vals = {"GNSSAltitude": np.rint((sub(alt)[sel] + 500.0) / 0.1) * 0.1,
                    "GNSSSpeed": np.rint(np.clip(sub(gnss_speed)[sel], 0, None) / 0.01) * 0.01,
                    "GNSSHeading": np.rint(sub(heading)[sel] / 0.0078125) * 0.0078125,
                    "GNSSSatellites": np.rint(rng.uniform(7, 14, sel.size))}
        elif m["name"] == "TRK_BMS1":
            volts = sub(np.full(trip.v.size, params.pack_voltage_v)) + rng.normal(0, 2.0, sel.size)
            amps = sub(trip.battery_kw)[sel] * 1000.0 / np.clip(volts, 1.0, None)
            vals = {"PackVoltage": np.rint(volts / 0.1) * 0.1,
                    "PackCurrent": np.rint(amps / 0.1) * 0.1,
                    "StateOfCharge": np.rint(sub(trip.soc)[sel] / 0.4) * 0.4,
                    "MotorTorque": np.rint(sub(trip.p_total)[sel] / 120.0),
                    "MotorSpeedRpm100": np.rint(sub(eng_rpm)[sel] / 100.0) * 100.0}
        else:
            continue

        frames = can_io.encode_message_vectorized(db, m["name"], vals)
        nf = frames.shape[0]
        ts = t_base + t_log[sel]

        # 随机丢帧 + 突发丢包（模拟总线负载抖动 / 隧道失联）。
        # 突发长度按**时间**给定再换算帧数 —— 否则 1 Hz 信号会一次丢 2 分钟。
        rate = float(m["rate_hz"])
        keep = rng.random(nf) >= drop_rate
        for _ in range(burst_drops):
            blen = max(1, int(round(rng.uniform(2.5, 8.0) * rate)))
            if nf > blen + 200:
                b0 = int(rng.integers(0, nf - blen - 100))
                keep[b0:b0 + blen] = False
        ts_all.append(ts[keep])
        ids_all.append(np.full(int(keep.sum()), m["can_id"], dtype=np.int64))
        dat_all.append(frames[keep])

    can_io.write_candump(out_path,
                         np.concatenate(ts_all),
                         np.concatenate(ids_all),
                         np.concatenate(dat_all, axis=0))
    trip.can_log = out_path
    return out_path


# ======================================================================
# 车队生成
# ======================================================================
FAULT_PLAN = [
    # (注入故障名, 相对权重, 参数覆盖)
    ("normal", 0, {}),
    ("tire_pressure_low", 8, dict(c_rr_scale=1.35)),
    # 激烈驾驶在卡车上的主要能耗代价是超速（风阻∝v³），而非更大的加速度
    ("aggressive_driving", 8, dict(style=1.75, speed_scale=1.12)),
    ("excessive_idling", 4, dict(idle_extra_s=620.0)),
    ("combustion_degraded", 4, dict(bsfc_scale=1.10)),
]


def build_fleet(n_days: int = 40, trips_per_day: int = 2,
                powertrain: str = "fuel", seed: int = 20241013) -> list[TripSpec]:
    """构造车队行程规格表，并按计划注入故障（保证 AI 评测有真值标签）。

    注入数量随车队规模等比缩放，保证始终有足够"正常"样本作为基线。
    """
    rng = np.random.default_rng(seed)
    total = n_days * trips_per_day

    ratios = [(name, n, kw) for name, n, kw in FAULT_PLAN if name != "normal"]
    n_target = int(round(total * 0.30))       # 约 30% 异常，贴近真实车队先验
    scale = n_target / max(sum(n for _, n, _ in ratios), 1)

    labels: list[tuple[str, dict]] = []
    for name, n, kw in ratios:
        labels.extend([(name, kw)] * max(1, int(round(n * scale))))
    while len(labels) > total - 4:            # 至少保留 4 个正常样本
        labels.pop()
    labels.extend([("normal", {})] * (total - len(labels)))
    rng.shuffle(labels)

    specs: list[TripSpec] = []
    for i in range(total):
        day = i // trips_per_day
        fault, kw = labels[i]
        base = dict(
            payload_kg=float(rng.uniform(5000, 25000)),
            style=float(rng.uniform(0.85, 1.2)),
            ambient_c=float(rng.uniform(4, 34)),
            route_seed=int(rng.integers(0, 6)),
        )
        base.update(kw)                       # 注入参数覆盖随机取值
        specs.append(TripSpec(
            trip_id=f"T{day:03d}-{i % trips_per_day + 1}",
            day=day,
            powertrain=powertrain,
            injected_fault=fault,
            **base,
        ))
    return specs
