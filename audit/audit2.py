#!/usr/bin/env python
"""补充审计实验 G~J。

G  峰值发动机功率：核实 README「所有车次 ≤ 347 kW」的论断
H  独立验证自研矢量化比特编码器 vs cantools 官方编码器（逐帧比对）
I  误差来源拆解：0.01%~0.05% 的"与真值误差"究竟证明了什么
J  decode_success_rate 的性质：它是回环自证还是独立的鲁棒性检验
"""
from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent / "prototype"
sys.path.insert(0, str(ROOT))

from src import can_io, energy, preprocess, simulator  # noqa: E402
from src import physics as ph  # noqa: E402
from src.physics import VehicleParams  # noqa: E402

CFG = ROOT / "config"
OUT = ROOT / "outputs"


def hr(t):
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}", flush=True)


# ======================================================================
def exp_G(params):
    hr("实验 G：峰值发动机功率 —— 核实「所有车次峰值 ≤ 347 kW（额定 353）」")
    specs = simulator.build_fleet(40, 2, "fuel")
    trips = [simulator.simulate_trip(s, params) for s in specs]
    rated = params.engine_rated_power_kw
    p_wheel_cap = rated * 1000.0 * 0.88

    rows = []
    for tr in trips:
        eng = ph.engine_power_from_wheel(tr.p_total, params)
        rows.append(dict(trip_id=tr.spec.trip_id, fault=tr.spec.injected_fault,
                         eng_peak_kw=float(eng.max()),
                         wheel_peak_kw=float(tr.p_traction.max()) / 1000.0))
    df = pd.DataFrame(rows)
    print(f"  额定功率 {rated} kW ｜ 仿真器设定的车轮功率上限 "
          f"{p_wheel_cap / 1000.0:.2f} kW（额定×0.88）")
    print(f"  80 车次峰值发动机功率：max={df.eng_peak_kw.max():.1f} kW  "
          f"中位数={df.eng_peak_kw.median():.1f} kW")
    print(f"  峰值车轮牵引功率：max={df.wheel_peak_kw.max():.2f} kW")
    print(f"\n  超过 README 声称的 347 kW：{int((df.eng_peak_kw > 347).sum())} 车次")
    print(f"  超过额定 {rated} kW：{int((df.eng_peak_kw > rated).sum())} 车次")
    print(f"  超过仿真器设定的车轮上限 {p_wheel_cap / 1000.0:.2f} kW："
          f"{int((df.wheel_peak_kw > p_wheel_cap / 1000.0).sum())} 车次")
    print("\n  超限车次明细（按峰值降序，取前 12）：")
    sub = df.sort_values("eng_peak_kw", ascending=False).head(12)
    print(f"    {'车次':<12}{'注入故障':<24}{'发动机峰值kW':>14}{'车轮峰值kW':>13}")
    for _, r in sub.iterrows():
        flag = "  ← 超额定" if r.eng_peak_kw > rated else ("  ← 超347" if r.eng_peak_kw > 347 else "")
        print(f"    {r.trip_id:<12}{r.fault:<24}{r.eng_peak_kw:>14.1f}{r.wheel_peak_kw:>13.2f}{flag}")
    print("\n  按故障类型统计峰值：")
    print(df.groupby("fault")["eng_peak_kw"].agg(["size", "mean", "max"]).round(1).to_string())


# ======================================================================
def exp_H():
    hr("实验 H：独立验证自研矢量化比特编码器（vs cantools 官方编码器）")
    db = can_io.load_dbc(CFG / "trucks_demo.dbc")
    rng = np.random.default_rng(12345)
    N = 300
    total_match = total_cmp = 0
    print(f"  {'报文':<12}{'帧数':>6}{'与官方编码器一致':>18}{'不一致':>9}   不一致样例")
    for msg in db.messages:
        vals = {}
        for sig in msg.signals:
            lo = sig.minimum if sig.minimum is not None else 0.0
            hi = sig.maximum if sig.maximum is not None else max(1.0, lo + 100.0)
            if sig.length >= 32:
                hi = min(hi, 1e7)
            vals[sig.name] = rng.uniform(lo, hi, N)
        mine = can_io.encode_message_vectorized(db, msg.name, vals)
        bad = 0
        sample = ""
        for k in range(N):
            ref = can_io.encode_message_reference(
                db, msg.name, {s: float(v[k]) for s, v in vals.items()})
            got = mine[k].tobytes()
            total_cmp += 1
            if got == ref:
                total_match += 1
            else:
                bad += 1
                if not sample:
                    sample = (f"官方={ref.hex().upper()} 自研={got.hex().upper()}")
        print(f"  {msg.name:<12}{N:>6}{N - bad:>18}{bad:>9}   {sample}")
    print(f"\n  → 逐帧比对 {total_cmp} 帧，一致 {total_match} 帧，"
          f"一致率 {total_match / total_cmp:.4%}")
    print("  这是对自研位打包实现的真实验证（build_dbc.py 未包含此项）。")


# ======================================================================
def exp_I(params):
    hr("实验 I：误差来源拆解 —— 「与真值误差 0.01%~0.05%」证明了什么")
    db = can_io.load_dbc(CFG / "trucks_demo.dbc")
    for trip_id in ("T000-2", "T005-1", "T000-1"):
        log = OUT / f"canlog_{trip_id}.log"
        spec = None
        for s in simulator.build_fleet(40, 2, "fuel"):
            if s.trip_id == trip_id:
                spec = s
                break
        tr = simulator.simulate_trip(spec, params)

        # 路线 1：完整报文链路（编码 -> 解码 -> 清洗 -> 能耗）
        decoded, dstat = can_io.decode_log(db, log)
        f_can = preprocess.build_frame(decoded, dstat)
        r_can = energy.analyze_frame(f_can, params, "fuel")

        # 路线 2：真值直通（无编解码、无量化、无噪声），同一套物理/特征代码
        from run_demo import _frame_from_truth
        f_tru = _frame_from_truth(tr, params)
        r_tru = energy.analyze_frame(f_tru, params, "fuel")

        # 路线 3：真值直通 + 真值坡度/质量（完全无估计误差）
        # 注意：真值数组是 20 Hz(DT)，信号帧是 10 Hz(LOG_HZ)，必须先对齐到帧网格。
        idx = np.clip((f_tru.t / simulator.DT).astype(int), 0, tr.v.size - 1)
        r_ide = energy.analyze_frame(f_tru, params, "fuel",
                                     mass_override=np.asarray(tr.mass)[idx],
                                     grade_override=np.asarray(tr.grade)[idx])

        t_fuel = tr.truth["fuel_l"]
        print(f"\n  [{trip_id}]  仿真真值油耗 {t_fuel:.4f} L ｜ "
              f"真值里程 {tr.truth['distance_km']:.4f} km")
        print(f"    {'路径':<34}{'油耗 L':>10}{'里程 km':>10}{'对真值误差':>12}")
        for tag, r in (("① 完整报文链路（含量化/噪声/丢帧）", r_can),
                       ("② 真值直通（无编解码，用估计坡度/质量）", r_tru),
                       ("③ 真值直通 + 真值坡度/质量", r_ide)):
            err = abs(r.fuel_l - t_fuel) / t_fuel * 100.0
            print(f"    {tag:<34}{r.fuel_l:>10.4f}{r.distance_km:>10.4f}{err:>11.4f}%")
        # 车轮侧分解对比
        e_tru = tr.truth["e_traction_wheel_kwh"]
        print(f"    车轮牵引能量：路径① {r_can.e_traction_wheel_kwh:.3f} kWh ｜ "
              f"真值 {e_tru:.3f} kWh ｜ 误差 "
              f"{abs(r_can.e_traction_wheel_kwh - e_tru) / e_tru * 100:.2f}%")
    print("\n  结论：路径② 与路径① 的差异 = 编解码/量化/噪声的贡献；")
    print("        路径③ 与路径② 的差异 = 坡度与质量估计误差的贡献。")
    print("        两者都很小，说明**链路保真**；但三条路径用的是同一套 physics.py，")
    print("        因此该指标不检验物理模型本身的正确性。")


# ======================================================================
def exp_J(params):
    hr("实验 J：decode_success_rate 的性质")
    db = can_io.load_dbc(CFG / "trucks_demo.dbc")
    log = OUT / "canlog_T000-2.log"
    ts, ids, data = can_io.read_candump(log)
    print(f"  原始日志 {ids.size:,} 帧，全部由同一份 DBC 编码产生")

    # (a) 原样
    _, st = can_io.decode_log(db, log)
    print(f"  (a) 原样解码                     成功率 {st['decode_success_rate']:.4%}  "
          f"未知ID {st['frames_unknown_id']}")

    # (b) 故意破坏 5% 帧的最后一个字节
    rng = np.random.default_rng(0)
    bad = rng.choice(ids.size, size=ids.size // 20, replace=False)
    data2 = data.copy()
    data2[bad, 7] ^= 0xFF
    tmp = Path(tempfile.gettempdir()) / "_premiles_corrupt.log"
    can_io.write_candump(tmp, ts, ids, data2)
    _, st2 = can_io.decode_log(db, tmp)
    print(f"  (b) 破坏 5% 帧的尾字节后          成功率 {st2['decode_success_rate']:.4%}  "
          f"失败 {st2['frames_failed']}")

    # (c) 注入未知 CAN ID
    ids3 = ids.copy()
    ids3[:500] = 0x12345678
    tmp3 = Path(tempfile.gettempdir()) / "_premiles_unknown.log"
    can_io.write_candump(tmp3, ts, ids3, data)
    _, st3 = can_io.decode_log(db, tmp3)
    print(f"  (c) 注入 500 帧未知 ID 后         成功率 {st3['decode_success_rate']:.4%}  "
          f"未知ID {st3['frames_unknown_id']}")

    print("\n  说明：成功率的分母是日志总帧数，未知 ID 也被计入分母 ——")
    print("        因此它能反映「信号完整性」，但原型中 100% 是同源编解码的必然结果。")
    print("        真正被检验的是自研位打包实现（见实验 H），而非真实车辆的解析鲁棒性。")
    tmp.unlink(missing_ok=True)
    tmp3.unlink(missing_ok=True)


def main():
    params = VehicleParams()
    print("[准备] 重建车队（真值仿真）")
    buf = io.StringIO()
    with redirect_stdout(buf):
        pass
    exp_G(params)
    exp_H()
    exp_I(params)
    exp_J(params)


if __name__ == "__main__":
    main()
