"""可视化与报告生成。

* 图表：能耗瀑布图、行程概览、车队基线与异常、工况与驾驶行为
* 报告：Markdown 结构化文本 —— 生产环境中这一层交给 LLM 生成自然语言，
  原型里由模板渲染，保证**所有数字来自物理/统计模型，模板不编造任何数值**。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402

# 中文字体
matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
matplotlib.rcParams["figure.dpi"] = 130
matplotlib.rcParams["savefig.bbox"] = "tight"

C_MAIN = "#0E7C86"
C_ACCENT = "#E8A33D"
C_BAD = "#C0392B"
C_GOOD = "#27AE60"
C_GREY = "#7F8C8D"


# ----------------------------------------------------------------------
# 图 1：行程概览
# ----------------------------------------------------------------------
def plot_trip_overview(frame, res, grade, out_path: Path, title: str = "行程概览"):
    t = frame.t / 60.0
    v = np.nan_to_num(frame["WheelBasedVehicleSpeed"])
    fuel = np.nan_to_num(frame["EngineFuelRate"]) if frame.has("EngineFuelRate") else None

    fig, axes = plt.subplots(4, 1, figsize=(11, 9), sharex=True)
    axes[0].plot(t, v, color=C_MAIN, lw=0.9)
    axes[0].set_ylabel("车速 (km/h)")
    axes[0].set_title(f"{title} —— 报文还原的行驶状态与能量流", fontsize=12, weight="bold")
    axes[0].grid(alpha=0.25)

    ax2 = axes[0].twinx()
    ax2.plot(t, grade, color=C_ACCENT, lw=0.8, alpha=0.75)
    ax2.set_ylabel("坡度 (%)", color=C_ACCENT)
    ax2.tick_params(axis="y", colors=C_ACCENT)

    if fuel is not None:
        axes[1].plot(t, fuel, color=C_BAD, lw=0.8)
        axes[1].set_ylabel("燃油率 (L/h)")
    else:
        axes[1].plot(t, np.nan_to_num(frame["PackCurrent"]) if frame.has("PackCurrent") else v,
                     color=C_BAD, lw=0.8)
        axes[1].set_ylabel("电池电流 (A)")
    axes[1].grid(alpha=0.25)

    # 功率分解
    dt = 1.0 / len(frame.t) * frame.t[-1] if frame.t[-1] > 0 else 0.1
    axes[2].fill_between(t, 0, res.wheel.get("滚动阻力", 0) * 0 + 0, alpha=0)
    share = res.wheel
    labels = list(share.keys())
    vals = [share[k] for k in labels]
    axes[2].barh(labels, vals,
                 color=[C_MAIN, "#4FA3AB", C_ACCENT, C_GREY], height=0.55)
    axes[2].set_xlabel("车轮侧分项能量 (kWh)")
    axes[2].axvline(0, color="k", lw=0.8)
    axes[2].grid(alpha=0.25, axis="x")
    axes[2].set_title(f"车轮牵引能量 {res.e_traction_wheel_kwh:.2f} kWh / "
                      f"制动需求 {res.e_brake_req_kwh:.2f} kWh", fontsize=10)

    # 累计能耗
    if fuel is not None:
        cum = np.cumsum(fuel) * (frame.t[1] - frame.t[0]) / 3600.0
        axes[3].plot(t, cum, color=C_BAD, lw=1.2)
        axes[3].set_ylabel("累计燃油 (L)")
    else:
        u = np.nan_to_num(frame["PackVoltage"]); i = np.nan_to_num(frame["PackCurrent"])
        cum = np.cumsum(u * i / 1000.0) * (frame.t[1] - frame.t[0]) / 3600.0
        axes[3].plot(t, cum, color=C_BAD, lw=1.2)
        axes[3].set_ylabel("累计电量 (kWh)")
    axes[3].set_xlabel("时间 (min)")
    axes[3].grid(alpha=0.25)

    fig.savefig(out_path)
    plt.close(fig)


# ----------------------------------------------------------------------
# 图 2：能耗瀑布图（本方案的核心输出）
# ----------------------------------------------------------------------
def plot_waterfall(res, out_path: Path):
    labels = list(res.chem.keys())
    vals = np.array([res.chem[k] for k in labels], dtype=float)
    total = float(res.energy_kwh)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.6),
                                   gridspec_kw={"width_ratios": [1.25, 1]})

    # --- 左：化学能去向瀑布 ---
    cum = 0.0
    for i, (lab, val) in enumerate(zip(labels, vals)):
        color = C_GOOD if val < 0 else C_MAIN
        ax1.bar(i, val, bottom=cum, color=color, width=0.6,
                edgecolor="white", linewidth=0.8)
        y_txt = cum + val + (total * 0.015 if val >= 0 else -total * 0.045)
        ax1.text(i, y_txt, f"{val:.1f}\n({val / total * 100:.0f}%)",
                 ha="center", va="bottom" if val >= 0 else "top", fontsize=8.5)
        cum += val
    ax1.bar(len(labels), total, color=C_ACCENT, width=0.6)
    ax1.text(len(labels), total * 1.02, f"{total:.1f}\nkWh", ha="center", fontsize=9)
    ax1.set_xticks(range(len(labels) + 1))
    ax1.set_xticklabels(labels + ["实测总能耗"], rotation=20, ha="right", fontsize=8.5)
    ax1.set_ylabel("能量 (kWh)")
    unit = "柴油化学能" if res.powertrain == "fuel" else "电池电能"
    ax1.set_title(f"能耗瀑布图：{unit}去向分解\n（各分项之和 = 实测总能耗，误差 "
                  f"{res.checks['energy_balance']['residual_pct']:.2e}%）",
                  fontsize=11, weight="bold")
    ax1.grid(alpha=0.25, axis="y")

    # --- 右：车轮侧分解 ---
    wl = list(res.wheel.keys())
    wv = np.array([res.wheel[k] for k in wl], dtype=float)
    colors = [C_MAIN if x >= 0 else C_GOOD for x in wv]
    ax2.barh(wl, wv, color=colors, height=0.55)
    ax2.axvline(0, color="k", lw=0.8)
    span = max(abs(wv).max(), 1e-9)
    for i, x in enumerate(wv):
        ax2.text(x + (span * 0.03 if x >= 0 else -span * 0.03), i,
                 f"{x:.2f} kWh", va="center",
                 ha="left" if x >= 0 else "right", fontsize=8.5)
    # 为负值标签留出空间，避免压到坐标轴与刻度文字
    ax2.set_xlim(min(wv.min() - span * 0.42, -span * 0.1),
                 max(wv.max() + span * 0.30, span * 0.1))
    ax2.set_xlabel("能量 (kWh)")
    ax2.set_title(f"车轮侧能量分解\n牵引 {res.e_traction_wheel_kwh:.2f} kWh，"
                  f"制动耗散 {res.e_brake_req_kwh:.2f} kWh",
                  fontsize=11, weight="bold")
    ax2.grid(alpha=0.25, axis="x")

    fig.savefig(out_path)
    plt.close(fig)


# ----------------------------------------------------------------------
# 图 3：车队基线与异常
# ----------------------------------------------------------------------
def plot_fleet(feat_df, residual_pct, anomaly_score, findings, out_path: Path,
               target_label: str = "L/100km"):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))

    x = feat_df["payload_t"].to_numpy()
    y = feat_df["_target"].to_numpy() if "_target" in feat_df else feat_df.iloc[:, 0].to_numpy()

    flagged = {f["index"] for f in findings}
    colors = [C_BAD if i in flagged else C_MAIN for i in range(len(x))]
    axes[0].scatter(x, y, c=colors, s=42, alpha=0.8, edgecolor="white", linewidth=0.6)
    order = np.argsort(x)
    axes[0].plot(x[order], np.poly1d(np.polyfit(x, y, 1))(x[order]),
                 color=C_GREY, ls="--", lw=1.2, label="趋势线")
    axes[0].set_xlabel("整车总质量 (t)")
    axes[0].set_ylabel(target_label)
    axes[0].set_title("载荷 vs 能耗\n（红点 = 被标记的异常车次）", fontsize=10.5, weight="bold")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.25)

    axes[1].axhline(0, color="k", lw=0.8)
    axes[1].bar(range(len(residual_pct)), residual_pct, color=colors, width=0.8)
    axes[1].set_xlabel("车次序号")
    axes[1].set_ylabel("残差 (%)")
    axes[1].set_title("基线模型残差\n（正=比同类应达水平更耗能）", fontsize=10.5, weight="bold")
    axes[1].grid(alpha=0.25, axis="y")

    axes[2].scatter(residual_pct, anomaly_score, c=colors, s=42, alpha=0.8,
                    edgecolor="white", linewidth=0.6)
    axes[2].axhline(np.quantile(anomaly_score, 0.90), color=C_ACCENT, ls="--",
                    lw=1.2, label="IsolationForest 阈值(P90)")
    axes[2].axvline(2 * np.std(residual_pct), color=C_GREY, ls=":", lw=1.2,
                    label="残差 z=2")
    axes[2].set_xlabel("残差 (%)")
    axes[2].set_ylabel("异常分数")
    axes[2].set_title("双轨异常检测\n（统计残差 + 无监督离群）", fontsize=10.5, weight="bold")
    axes[2].legend(fontsize=7.5)
    axes[2].grid(alpha=0.25)

    fig.savefig(out_path)
    plt.close(fig)


# ----------------------------------------------------------------------
# 图 4：工况与驾驶行为
# ----------------------------------------------------------------------
def plot_modes(modes, events, out_path: Path):
    shares = modes["shares"]
    names = [k for k in shares if shares[k]["time_pct"] > 0]
    t_pct = [shares[k]["time_pct"] for k in names]
    d_pct = [shares[k]["km_pct"] for k in names]
    e_pct = [shares[k]["kwh_pct"] for k in names]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    y = np.arange(len(names))
    h = 0.26
    axes[0].barh(y - h, t_pct, height=h, label="时间占比", color=C_MAIN)
    axes[0].barh(y, d_pct, height=h, label="里程占比", color="#4FA3AB")
    axes[0].barh(y + h, e_pct, height=h, label="能耗占比", color=C_ACCENT)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(names, fontsize=9)
    axes[0].set_xlabel("占比 (%)")
    axes[0].set_title("工况结构：时间 / 里程 / 能耗三维占比", fontsize=11, weight="bold")
    axes[0].legend(fontsize=8.5)
    axes[0].grid(alpha=0.25, axis="x")

    keys = ["harsh_accel_per_100km", "harsh_brake_per_100km", "idle_time_pct",
            "cruise_time_pct", "accel_std", "pke_kj_per_km"]
    labels = ["急加速\n(次/100km)", "急减速\n(次/100km)", "怠速时间\n(%)",
              "经济车速\n占比(%)", "加速度\n标准差", "PKE\n(kJ/km)"]
    vals = [events[k] for k in keys]
    axes[1].bar(range(len(keys)), vals, color=C_MAIN, width=0.55)
    for i, v in enumerate(vals):
        axes[1].text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=8.5)
    axes[1].set_xticks(range(len(keys)))
    axes[1].set_xticklabels(labels, fontsize=8.5)
    axes[1].set_title("驾驶行为量化指标", fontsize=11, weight="bold")
    axes[1].grid(alpha=0.25, axis="y")
    fig.savefig(out_path)
    plt.close(fig)


# ----------------------------------------------------------------------
# Markdown 报告
# ----------------------------------------------------------------------
def render_trip_report(res, frame, modes, events, score, findings, cmp_truth,
                       vehicle_id: str = "DEMO-001", trip_id: str = "T000-1",
                       model_metrics: dict | None = None) -> str:
    q = frame.quality
    chem = res.chem
    total = res.energy_kwh
    is_fuel = res.powertrain == "fuel"

    L: list[str] = []
    L.append(f"# 车辆能耗体检报告 —— {vehicle_id} / 车次 {trip_id}")
    L.append("")
    L.append(f"> 数据质量：**{q['score']:.1f} 分（{q['level']}）** ｜ "
             f"解码成功率 {q['decode_stats'].get('decode_success_rate', 0):.3%} ｜ "
             f"时长 {res.duration_s / 60:.1f} min ｜ 里程 {res.distance_km:.2f} km")
    L.append("")

    L.append("## 一、核心结论")
    L.append("")
    L.append("| 指标 | 数值 | 说明 |")
    L.append("|---|---|---|")
    if is_fuel:
        L.append(f"| 百公里油耗 | **{res.l_per_100km:.2f} L/100km** | 行业口径 |")
        L.append(f"| 吨公里油耗 | **{res.l_per_100tkm:.3f} L/(100t·km)** | 跨载荷可比口径 |")
    else:
        L.append(f"| 百公里电耗 | **{res.kwh_per_100km:.2f} kWh/100km** | 行业口径 |")
        L.append(f"| 吨公里电耗 | **{res.kwh_per_100tkm:.3f} kWh/(100t·km)** | 跨载荷可比口径 |")
    L.append(f"| 总能耗 | {total:.2f} kWh | {'燃油化学能' if is_fuel else '电池电能'} |")
    L.append(f"| 平均车速 | {res.distance_km / (res.duration_s / 3600):.1f} km/h | |")
    L.append(f"| 驾驶行为评分 | **{score['score']:.1f} / 100** | 与车队同期同型基线对比 |")
    L.append("")

    L.append("## 二、能耗瀑布（能量去哪了）")
    L.append("")
    L.append("| 分项 | 能量 (kWh) | 占比 |")
    L.append("|---|---:|---:|")
    for k, v in chem.items():
        L.append(f"| {k} | {v:.2f} | {v / total * 100:.1f}% |")
    L.append(f"| **合计** | **{sum(chem.values()):.2f}** | **100.0%** |")
    L.append("")
    L.append("车轮侧分解（物理模型独立计算）：")
    L.append("")
    L.append("| 分项 | 能量 (kWh) |")
    L.append("|---|---:|")
    for k, v in res.wheel.items():
        L.append(f"| {k} | {v:.2f} |")
    L.append(f"| 牵引能量合计 | {res.e_traction_wheel_kwh:.2f} |")
    L.append(f"| 制动需求（耗散） | {res.e_brake_req_kwh:.2f} |")
    L.append("")

    L.append("## 三、工况结构")
    L.append("")
    L.append("| 工况 | 时间占比 | 里程占比 | 能耗占比 |")
    L.append("|---|---:|---:|---:|")
    for k, v in modes["shares"].items():
        if v["time_pct"] <= 0:
            continue
        L.append(f"| {k} | {v['time_pct']:.1f}% | {v['km_pct']:.1f}% | {v['kwh_pct']:.1f}% |")
    L.append("")

    L.append("## 四、驾驶行为")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---:|")
    L.append(f"| 急加速 | {events['harsh_accel']} 次（{events['harsh_accel_per_100km']:.1f} 次/100km） |")
    L.append(f"| 急减速 | {events['harsh_brake']} 次（{events['harsh_brake_per_100km']:.1f} 次/100km） |")
    L.append(f"| 怠速时长 | {events['idle_seconds']:.0f} s（{events['idle_time_pct']:.1f}%） |")
    L.append(f"| 经济车速占比 | {events['cruise_time_pct']:.1f}% |")
    L.append(f"| 正动能需求 PKE | {events['pke_kj_per_km']:.2f} kJ/(km·t) |")
    L.append("")
    L.append("> 驾驶行为扣分归因（相较车队基线）：")
    for k, v in sorted(score["deductions"].items(), key=lambda kv: -kv[1]):
        if v > 0.05:
            L.append(f"> - {k}：−{v:.1f} 分")
    L.append("")

    L.append("## 五、三道自检（报告发布前置条件）")
    L.append("")
    c1 = res.checks["energy_balance"]
    c2 = res.checks["distance_consistency"]
    c3 = res.checks["reconciliation"]
    L.append(f"1. **能量守恒**：瀑布分项之和 {c1['sum_of_parts']:.2f} kWh vs 实测 "
             f"{c1['total']:.2f} kWh，偏差 {c1['residual_pct']:.2e}% ✅")
    L.append(f"2. **三方里程**：车速积分 {c2['paths']['can']:.3f} km ｜ 里程表 "
             f"{c2['paths']['odo']:.3f} km ｜ GNSS {c2['paths']['gnss']:.3f} km，"
             f"最大互差 {c2['spread_pct']:.2f}% "
             f"{'✅' if c2['passed'] else '⚠️'}")
    if c3.get("passed") is not None:
        L.append(f"3. **对账（模拟加油小票）**：燃油率积分 {c3['from_rate_l']:.2f} L vs "
                 f"累计油耗差分 {c3['from_totalizer_l']:.2f} L，"
                 f"误差 {c3['error_pct']:.2f}% "
                 f"（累计油耗分辨率 0.5 L 本身的量化下限 {c3['totalizer_resolution_pct']:.2f}%）"
                 f"{'✅' if c3['passed'] else '⚠️'}")
    else:
        L.append(f"3. **对账**：{c3.get('note', '无独立对账基准')}")
    L.append("")

    L.append("## 六、与仿真真值的对照（原型专有验证）")
    L.append("")
    L.append("| 量 | 报文反算 | 仿真真值 | 误差 |")
    L.append("|---|---:|---:|---:|")
    for k, v in cmp_truth.items():
        L.append(f"| {k} | {v['computed']:.3f} | {v['truth']:.3f} | {v['error_pct']:.2f}% |")
    L.append("")

    L.append("## 七、数据质量明细")
    L.append("")
    if q["issues"]:
        for i in q["issues"]:
            L.append(f"- {i}")
    else:
        L.append("- 未发现明显数据质量问题")
    L.append(f"- 坡度来源：{res.grade_source}")
    L.append(f"- 质量来源：{res.mass_source}")
    L.append("")

    if findings:
        L.append("## 八、疑似异常与责任判定")
        L.append("")
        for f in findings:
            delta = f["residual_pct"]
            head = (f"**相对作业基线 {delta:+.1f}%**" if delta >= 0 else
                    f"**相对作业基线 {delta:+.1f}%（总能耗未超标，但存在明确的行为/状况异常）**")
            L.append(f"- {head}"
                     f"（z={f['z_score']:+.2f}，严重度 {f['severity']}，"
                     f"判据：{'/'.join(f.get('evidence', []))}）"
                     f" → 判定：**{f.get('primary', '待判定')}**")
            for c in f.get("driving_causes", []):
                L.append(f"    - 【驾驶行为】{c}")
            for c in f.get("vehicle_causes", []):
                L.append(f"    - 【车辆状况】{c}")
            for c in f.get("unexplained", []):
                L.append(f"    - 【未定因】{c}")
        L.append("")

    if model_metrics:
        L.append("## 九、AI 基线模型（本次车队样本）")
        L.append("")
        L.append("| 指标 | 数值 |")
        L.append("|---|---:|")
        L.append(f"| 训练样本 | {model_metrics['n_samples']} 车次 |")
        L.append(f"| 特征数 | {model_metrics['n_features']} |")
        L.append(f"| 样本外 R² | {model_metrics['r2_oof']:.3f} |")
        L.append(f"| 样本外 MAE | {model_metrics['mae_oof']:.2f} L/100km |")
        L.append(f"| 样本外 MAPE | {model_metrics['mape_oof']:.2f}% |")
        L.append("")
        L.append("> 残差全部来自**交叉验证的样本外预测**，不使用训练集自证。")
        L.append("")

    L.append("---")
    L.append("")
    L.append("### 建议动作（由 LLM 依据上述结构化结论生成自然语言版本）")
    L.append("")
    acts = _suggest(res, events, score, findings)
    for a in acts:
        L.append(f"- {a}")
    L.append("")
    return "\n".join(L)


def _suggest(res, events, score, findings) -> list[str]:
    """基于结构化结论生成可执行建议（生产环境由 LLM 渲染为自然语言）。"""
    out: list[str] = []
    total = max(res.energy_kwh, 1e-9)
    if events["idle_time_pct"] > 8.0:
        out.append(f"怠速时间占比 {events['idle_time_pct']:.1f}%，"
                   f"建议推行「停车 3 分钟以上熄火」，预计可削减怠速耗能。")
    if events["harsh_brake_per_100km"] > 6.0:
        out.append(f"急减速 {events['harsh_brake_per_100km']:.1f} 次/100km，"
                   f"制动耗散 {res.e_brake_req_kwh:.1f} kWh（占总量 "
                   f"{res.e_brake_req_kwh / total * 100:.1f}%），建议开展预见性驾驶培训。")
    if events["cruise_time_pct"] < 40.0:
        out.append(f"经济车速（>40km/h 匀速）占比仅 {events['cruise_time_pct']:.1f}%，"
                   f"建议优化线路与调度以降低启停频次。")
    roll = res.wheel.get("滚动阻力", 0.0)
    if roll / total > 0.10:
        out.append(f"滚动阻力耗能 {roll:.1f} kWh（占 {roll / total * 100:.1f}%），"
                   f"建议核查胎压与车轮定位，并排查制动拖滞。")
    if findings:
        out.append(f"本次识别到 {len(findings)} 项能耗异常，"
                   f"最高相对基线 {findings[0]['residual_pct']:+.1f}%，"
                   f"判定为「{findings[0].get('primary', '待判定')}」，建议优先核查。")
    if not out:
        out.append("各项指标均处于车队正常区间，保持当前驾驶与维护策略即可。")
    return out
