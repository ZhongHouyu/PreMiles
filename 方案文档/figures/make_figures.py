"""方案配图生成脚本（可复现）。

用途
----
为 `方案文档/01、02、03、05、06` 生成配图。**所有数值都来自真实产物或审计复算**，
不做任何示意性编造；凡属"定性示意"的图，图内必须显式标注。

数据来源
--------
* `prototype/outputs/车队汇总.csv`        80 车次真实明细（残差、eta、异常标记…）
* `prototype/outputs/run_log.txt`         端到端运行汇总（对账误差、里程互差…）
* `audit/audit.py` 实验 A                 参数扰动敏感性（26.87 → 31.23 / 30.47 / 42.06）
* `audit/audit3.py` 实验 K/M               可辨识性、量化下限
* `方案文档/04_独立复现与技术审计报告.md`    上述复算值的文字出处

运行
----
    python make_figures.py          # 输出 PNG 到本目录

依赖：matplotlib、numpy、pandas（与 prototype 相同）。
"""

from __future__ import annotations

import warnings
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

# ---------------------------------------------------------------- 全局样式
matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
matplotlib.rcParams["figure.dpi"] = 160
matplotlib.rcParams["savefig.bbox"] = "tight"
matplotlib.rcParams["savefig.facecolor"] = "white"
matplotlib.rcParams["axes.edgecolor"] = "#BBBBBB"
matplotlib.rcParams["axes.labelcolor"] = "#222222"
matplotlib.rcParams["text.color"] = "#222222"

C_MAIN = "#0E7C86"      # 主色（原型同款）
C_ACCENT = "#E8A33D"
C_BAD = "#C0392B"
C_GOOD = "#27AE60"
C_GREY = "#7F8C8D"
C_BG = "#F4F7F8"
CAP_Y = -0.27          # 图下说明文字的统一纵坐标（避免与 x 轴标签压字）

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # …/PreMiles
OUTPUTS = ROOT / "prototype" / "outputs"
CSV = OUTPUTS / "车队汇总.csv"


def save(fig, name: str) -> None:
    path = HERE / name
    fig.savefig(path)
    plt.close(fig)
    # 注意：这里刻意只用 ASCII 输出。中文 Windows 控制台默认 GBK，
    # 打印 "✓" 之类的字符会抛 UnicodeEncodeError（原型 run_demo.py 踩过同一个坑）。
    print(f"  [OK] {name}")


def box(ax, x, y, w, h, text, fc, ec, fs=9.5, tc="#FFFFFF", weight="bold"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                linewidth=1.2, facecolor=fc, edgecolor=ec))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=tc, weight=weight, linespacing=1.5)


def arrow(ax, p0, p1, color=C_GREY, style="-|>", lw=1.4, rad=0.0):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle=style, mutation_scale=13,
                                 linewidth=lw, color=color,
                                 connectionstyle=f"arc3,rad={rad}"))


# ================================================================ 图 1 六步法
def fig_six_steps():
    fig, ax = plt.subplots(figsize=(14.6, 4.5))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    steps = [
        ("① 拆", "能量瀑布\n总能耗 → 8 个分项", C_MAIN),
        ("② 比", "公平基线残差\n剥离工况·载荷·环境", "#11707A"),
        ("③ 反", "参数反标定\n滚阻·风阻·效率·内阻", "#1B6E8C"),
        ("④ 敏", "灵敏度与优先级\nS_p = 该分项能量份额", "#8E6C1F"),
        ("⑤ 指", "降耗方向清单\n设计·VCU 策略·阈值", C_ACCENT),
        ("⑥ 验", "闭环验证\n台架·HIL·实车 A/B", C_GOOD),
    ]
    w, gap = 0.152, 0.016
    for i, (title, sub, color) in enumerate(steps):
        x = 0.004 + i * (w + gap)
        box(ax, x, 0.52, w, 0.30, f"{title}\n{sub}", color, color, fs=9.2)
        if i < len(steps) - 1:
            arrow(ax, (x + w + 0.002, 0.67), (x + w + gap - 0.002, 0.67), color="#95A5A6", lw=1.6)

    ax.text(0.5, 0.33, "先物理后 AI　·　先账目后归因　·　先剥离不可控再谈可控　·　判不出来就说不出来",
            ha="center", fontsize=11.5, color="#222222", weight="bold")
    ax.text(0.5, 0.16, "每一步都对应一个交付物与一个判据；凡「残差恒为 0」的是恒等式，凡「能对上独立观测量」的才是验证",
            ha="center", fontsize=9.6, color=C_GREY)
    ax.text(0.5, 0.045, "把「能耗高」从一句抱怨，变成一张按性价比排序、可标定、可回归验证的改进清单",
            ha="center", fontsize=10.2, color=C_MAIN, weight="bold")
    save(fig, "fig01_six_steps.png")


# ================================================================ 图 2 架构
def fig_architecture():
    fig, ax = plt.subplots(figsize=(13.6, 6.4))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    layers = [
        ("① 采集接入层", "路试记录 MDF4 / BLF / ASC　·　台架与转鼓　·　量产车 T-Box 回传", 0.845, C_MAIN),
        ("② 存储层", "原始报文（对象存储）　+　时序库 TDengine　+　分析库 ClickHouse / DuckDB　+　标定版本库", 0.700, "#11707A"),
        ("③ 解析与治理层", "DBC / A2L 解码 → 时间对齐 → 重采样 10 Hz → 清洗 → 质量分 → 单位归一 → 信号补全", 0.555, "#1B6E8C"),
        ("④ 物理与模型层", "拆｜能量瀑布　比｜公平基线残差　反｜参数反标定　敏｜灵敏度排序　指｜方向清单　验｜回归基线\n"
                            "AI：GBDT 基线　·　IsolationForest 异常检测　·　单参考点贡献分解　·　CUSUM 变点检测", 0.345, "#8E6C1F"),
        ("⑤ 应用层", "能耗归因报告　·　降耗方向清单　·　参数一致性看板　·　版本 A/B 对比　·　工况库导出（Simulink / HIL / 台架）", 0.175, C_ACCENT),
    ]
    for title, body, y, color in layers:
        h = 0.115 if "\n" not in body else 0.155
        ax.add_patch(FancyBboxPatch((0.03, y), 0.94, h,
                                    boxstyle="round,pad=0.008,rounding_size=0.012",
                                    linewidth=1.2, facecolor=C_BG, edgecolor=color))
        ax.text(0.045, y + h - 0.028, title, fontsize=11, weight="bold", color=color, va="top")
        ax.text(0.20, y + h / 2 - (0.012 if "\n" in body else 0.0), body,
                fontsize=9.0, color="#333333", va="center", linespacing=1.6)

    ax.text(0.5, 0.055, "复用现有 T-Box / 记录仪 / 路试数据，不新增车载硬件；分析侧可完全私有化部署",
            ha="center", fontsize=9.6, color=C_GREY)
    ax.text(0.5, 0.965, "整车能耗归因与降耗方向：五层架构", ha="center",
            fontsize=13, weight="bold", color=C_MAIN)
    save(fig, "fig02_architecture.png")


# ================================================================ 图 3 灵敏度
def fig_sensitivity():
    labels = ["基准\n（铭牌参数）", "风阻系数\nC_d × 1.6", "迎风面积\nA_f × 1.5",
              "滚阻系数\nC_rr × 3.0", "整备质量参数\n+8000 kg"]
    vals = [26.87, 31.23, 30.47, 42.06, 26.87]
    delta = ["—", "+16.2%", "+13.4%", "+56.5%", "0%\n（质量取自信号）"]
    colors = [C_MAIN, C_ACCENT, C_ACCENT, C_BAD, C_GREY]

    fig, ax = plt.subplots(figsize=(10.6, 5.6))
    bars = ax.bar(labels, vals, color=colors, width=0.56, zorder=3)
    for b, v, d in zip(bars, vals, delta):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.7, f"{v:.2f} kWh",
                ha="center", fontsize=9.4, weight="bold", color="#222222")
        ax.text(b.get_x() + b.get_width() / 2, v / 2, d, ha="center", va="center",
                fontsize=10.5, weight="bold", color="#FFFFFF")
    ax.axhline(26.87, color=C_MAIN, linewidth=1.0, linestyle=":", zorder=2)
    ax.set_ylabel("牵引能耗（kWh）", fontsize=10.5)
    ax.set_ylim(0, 50)
    ax.grid(axis="y", color="#E5E5E5", zorder=0)
    ax.set_axisbelow(True)
    ax.set_title("参数扰动敏感性：同一份报文日志，开环重算（L2 审计实验 A）",
                 fontsize=12, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "全部情形下「账目自洽检查」均通过 —— 账目自洽 ≠ 物理正确，物理校验必须靠独立观测量\n"
            "（滑行试验反标滚阻 · 地磅称重校核质量 · 长巡航段或风洞反标 C_d·A）",
            transform=ax.transAxes, ha="center", fontsize=9.4, color=C_BAD, linespacing=1.7)
    save(fig, "fig03_sensitivity.png")


# ================================================================ 图 4 份额一致
def fig_share_consistency():
    names = ["由 C_d × 1.6\n反解 w_aero", "由 A_f × 1.5\n反解 w_aero", "由 C_rr × 3.0\n反解 w_roll"]
    vals = [27.0, 26.8, 28.3]
    colors = [C_MAIN, C_MAIN, C_ACCENT]

    fig, ax = plt.subplots(figsize=(9.2, 5.0))
    bars = ax.bar(names, vals, color=colors, width=0.5, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.35, f"{v:.1f}%",
                ha="center", fontsize=11.5, weight="bold", color="#222222")
    ax.annotate("", xy=(0, 27.0), xytext=(1, 26.8),
                arrowprops=dict(arrowstyle="<->", color=C_GOOD, lw=1.6))
    ax.text(0.5, 29.4, "两次独立扰动给出同一份额（差 0.2 pp）\n→ 证明 E_aero ∝ C_d · A_f",
            ha="center", fontsize=10, color=C_GOOD, weight="bold", linespacing=1.6)
    ax.set_ylabel("反解出的能量份额（%）", fontsize=10.5)
    ax.set_ylim(0, 36)
    ax.grid(axis="y", color="#E5E5E5", zorder=0)
    ax.set_axisbelow(True)
    ax.set_title("灵敏度定理的数值验证：S_p = 该分项能量份额（L2 审计实验 A）",
                 fontsize=11.6, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "欧拉齐次函数定理：能量项一次齐次 → 相对灵敏度等于能量占比\n"
            "→ 降耗方向可以「排序」，而不是「罗列」",
            transform=ax.transAxes, ha="center", fontsize=9.4, color=C_GREY, linespacing=1.7)
    save(fig, "fig04_share_consistency.png")


# ================================================================ 图 5 可辨识性
def fig_identifiability_theory():
    k = np.linspace(1.0, 1.6, 200)
    fig, ax = plt.subplots(figsize=(9.6, 5.6))
    for w, ls, color in [(0.20, "--", "#9BB7C4"), (0.28, "-", C_MAIN), (0.40, "--", "#9BB7C4")]:
        rho = 1.0 / (1.0 + (k - 1.0) * w)
        ax.plot(k, rho, ls, color=color, lw=2.2 if w == 0.28 else 1.4,
                label=f"w = {w:.2f}" + ("（本车次）" if w == 0.28 else ""))

    ax.plot([1.35], [0.911], "o", color=C_BAD, markersize=9, zorder=5)
    ax.annotate("滚阻 +35%（k = 1.35）\n≡ 效率 −8.9%（ρ = 0.911）",
                xy=(1.35, 0.911), xytext=(1.415, 0.80),
                fontsize=10, color=C_BAD, weight="bold",
                arrowprops=dict(arrowstyle="-|>", color=C_BAD, lw=1.5), linespacing=1.6)
    ax.axhspan(0.90, 0.93, color=C_ACCENT, alpha=0.13, zorder=0)
    ax.text(1.015, 0.9145, "改装配件/胎压不当 → 滚阻劣化\n与\n发动机/传动效率下降\n在此带内无法区分",
            fontsize=9.2, color="#8A6D1F", linespacing=1.6)

    ax.set_xlabel("滚阻倍率 k（实际 / 设计）", fontsize=10.5)
    ax.set_ylabel("等效效率倍率 ρ", fontsize=10.5)
    ax.set_ylim(0.75, 1.02)
    ax.grid(color="#EDEDED")
    ax.legend(fontsize=9, loc="lower left")
    ax.set_title("可辨识性退化：ρ = 1 / [1 + (k − 1) · w]　一维观测量只能识别一个参数组合",
                 fontsize=11.4, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "实测印证（L1）：胎压不足 vs 燃烧恶化的 η_engine_est 中位数 = 0.3122 vs 0.3125，最优单阈值判别仅 75.0%（随机 66.7%）\n"
            "出路只有两条：增加观测维度（胎压 / 称重 / 台架）或改变实验设计（滑行试验单独暴露阻力项）",
            transform=ax.transAxes, ha="center", fontsize=9.3, color=C_GREY, linespacing=1.7)
    save(fig, "fig05_identifiability.png")


# ================================================================ 图 6 量化下限
def fig_quantization_floor():
    d = np.linspace(1.0, 30.0, 300)
    floor = 0.5 / d * 100.0
    fig, ax = plt.subplots(figsize=(9.8, 5.6))
    ax.plot(d, floor, color=C_MAIN, lw=2.2, label="量化下限 ε_floor = q / Δ（q = 0.5 L/bit）")
    ax.axhline(5.0, color=C_BAD, lw=1.4, ls="--", label="对账目标 5%")

    trips = [(8.80, 3.57, "T000-2"), (12.64, 1.08, "T005-1"), (16.18, 1.10, "T000-1")]
    for x, y, name in trips:
        ax.plot([x], [0.5 / x * 100], "o", color=C_GREY, markersize=7, zorder=4)
        ax.plot([x], [y], "D", color=C_GOOD, markersize=8, zorder=5)
    ax.plot([], [], "o", color=C_GREY, label="理论下限（该行程）")
    ax.plot([], [], "D", color=C_GOOD, label="实测对账误差（3 个演示车次）")

    ax.annotate("T005-1：Δ = 12.64 L\n下限 3.96%，实测 1.08%", xy=(12.64, 1.08),
                xytext=(15.2, 3.4), fontsize=9.4, color=C_GOOD, weight="bold",
                arrowprops=dict(arrowstyle="-|>", color=C_GOOD, lw=1.3), linespacing=1.6)
    ax.annotate("短行程：Δ 小 → 下限迅速升高\n（8.8 L 时下限 5.68%）", xy=(8.8, 5.68),
                xytext=(3.2, 12.0), fontsize=9.4, color=C_BAD,
                arrowprops=dict(arrowstyle="-|>", color=C_BAD, lw=1.3), linespacing=1.6)

    ax.set_xlabel("对账窗口内的燃油消耗 Δ（L）", fontsize=10.5)
    ax.set_ylabel("相对误差（%）", fontsize=10.5)
    ax.set_ylim(0, 20)
    ax.grid(color="#EDEDED")
    ax.legend(fontsize=8.8, loc="upper right")
    ax.set_title("量化下限 ε_floor = q / Δ：短行程对账在数学上不可能准",
                 fontsize=11.8, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y, "结论：必须按加油周期做长窗口聚合 —— 这是口径约束，不是工程偏好",
            transform=ax.transAxes, ha="center", fontsize=9.6, color=C_GREY)
    save(fig, "fig06_quantization_floor.png")


# ================================================================ 图 7 样本量
def fig_sample_size():
    delta = np.linspace(0.5, 5.0, 400)
    fig, ax = plt.subplots(figsize=(9.6, 5.4))
    for sd, color, ls in [(1.0, C_GOOD, "--"), (2.0, C_MAIN, "-"), (3.0, C_BAD, "--")]:
        n = 7.85 * sd ** 2 / delta ** 2
        ax.plot(delta, n, color=color, lw=2.0 if sd == 2.0 else 1.5, ls=ls,
                label=f"σ_d = {sd:.0f}%")
    ax.plot([1.0], [7.85 * 4 / 1.0], "o", color=C_MAIN, markersize=9, zorder=5)
    ax.annotate("Δ = 1%，σ_d = 2%\n→ n ≈ 32 组配对车次",
                xy=(1.0, 31.4), xytext=(1.6, 55), fontsize=10, color=C_MAIN, weight="bold",
                arrowprops=dict(arrowstyle="-|>", color=C_MAIN, lw=1.5), linespacing=1.6)
    ax.plot([2.0], [7.85 * 4 / 4.0], "o", color=C_ACCENT, markersize=8, zorder=5)
    ax.annotate("Δ = 2% → n ≈ 8", xy=(2.0, 7.85), xytext=(2.35, 18),
                fontsize=9.6, color="#8A6D1F",
                arrowprops=dict(arrowstyle="-|>", color=C_ACCENT, lw=1.3))
    ax.set_xlabel("待验证的能耗改善 Δ（%）", fontsize=10.5)
    ax.set_ylabel("所需配对车次数 n", fontsize=10.5)
    ax.set_ylim(0, 80)
    ax.grid(color="#EDEDED")
    ax.legend(fontsize=9)
    ax.set_title("n ≈ 7.85 · σ_d² / Δ²：验证 1% 的改善需要几十组同工况配对车次",
                 fontsize=11.4, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "α = 0.05、功效 0.8；σ_d 为同车同工况配对差值的标准差（L4 测算假设，待试点标定）\n"
            "→ 必须建「真实工况库 + 回放」，而不是「跑一圈看看」",
            transform=ax.transAxes, ha="center", fontsize=9.3, color=C_GREY, linespacing=1.7)
    save(fig, "fig07_sample_size.png")


# ================================================================ 图 8 残差分布
def fig_residual_distribution():
    df = pd.read_csv(CSV, encoding="utf-8")
    order = ["normal", "aggressive_driving", "excessive_idling",
             "tire_pressure_low", "combustion_degraded"]
    zh = {"normal": "正常\n(56)", "aggressive_driving": "激烈驾驶\n(8)",
          "excessive_idling": "长时间怠速\n(4)", "tire_pressure_low": "胎压不足\n(8)",
          "combustion_degraded": "燃烧恶化\n(4)"}
    groups = [df.loc[df["injected_fault"] == g, "residual_total_pct"].to_numpy() for g in order]

    fig, ax = plt.subplots(figsize=(10.4, 5.8))
    bp = ax.boxplot(groups, patch_artist=True, widths=0.5, showfliers=False,
                    medianprops=dict(color="#222222", lw=1.6))
    for patch, g in zip(bp["boxes"], order):
        patch.set_facecolor("#DCE9EA" if g == "normal" else "#F6E2C8")
        patch.set_edgecolor(C_MAIN if g == "normal" else C_ACCENT)
        patch.set_alpha(0.95)
    rng = np.random.default_rng(7)
    for i, (g, vals) in enumerate(zip(order, groups), start=1):
        flagged = df.loc[df["injected_fault"] == g, "flagged"].to_numpy().astype(bool)
        ax.scatter(i + rng.uniform(-0.13, 0.13, vals.size), vals,
                   s=26, c=np.where(flagged, C_BAD, C_GREY), zorder=4,
                   edgecolors="white", linewidths=0.6)
    ax.axhline(0, color="#999999", lw=1.0, ls=":")
    ax.set_xticklabels([zh[g] for g in order], fontsize=9.4)
    ax.set_ylabel("公平基线残差 r（%）", fontsize=10.5)
    ax.grid(axis="y", color="#EDEDED")
    ax.scatter([], [], s=30, c=C_BAD, label="被标记为疑似异常")
    ax.scatter([], [], s=30, c=C_GREY, label="未标记")
    ax.legend(fontsize=9, loc="upper left")
    ax.set_title("车队 80 车次的公平基线残差（真实产物 车队汇总.csv）",
                 fontsize=12, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "标记 19 条 / 正常 56 车次零误报；胎压不足平均 +6.5%、燃烧恶化 +9.9%、长时间怠速 −1.4%（L1）\n"
            "注意：残差为负不代表「没问题」—— 长时间怠速的能耗被基线部分吸收，需靠物理规则判据抓取",
            transform=ax.transAxes, ha="center", fontsize=9.3, color=C_GREY, linespacing=1.7)
    save(fig, "fig08_residual_distribution.png")


# ================================================================ 图 9 检测性能
def fig_detection_performance():
    metric = ["查准率 P", "查全率 R"]
    full = [100.0, 79.2]
    half = [100.0, 70.8]
    x = np.arange(len(metric))
    w = 0.34
    fig, ax = plt.subplots(figsize=(9.0, 5.2))
    b1 = ax.bar(x - w / 2, full, w, color=C_MAIN, label="全量自评口径（L1）", zorder=3)
    b2 = ax.bar(x + w / 2, half, w, color=C_ACCENT, label="折半交叉验证（L2）", zorder=3)
    for bs in (b1, b2):
        for b in bs:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.4,
                    f"{b.get_height():.1f}%", ha="center", fontsize=10.5, weight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(metric, fontsize=11)
    ax.set_ylim(0, 118)
    ax.set_ylabel("百分比（%）", fontsize=10.5)
    ax.grid(axis="y", color="#EDEDED", zorder=0)
    ax.set_axisbelow(True)
    ax.legend(fontsize=9.4, loc="lower right")
    ax.set_title("异常检测：查准率稳健，查全率存在 8.4 个百分点的口径乐观",
                 fontsize=11.6, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "TP = 19，FP = 0，FN = 5，TN = 56；全量自评 F1 = 0.884；56 个正常车次零误报（查准率 5 次折半仍为 100%）\n"
            "检出主力是物理规则：19 条标记中纯统计判据仅贡献 3 条（单用查全率 12.5%）",
            transform=ax.transAxes, ha="center", fontsize=9.2, color=C_GREY, linespacing=1.7)
    save(fig, "fig09_detection.png")


# ================================================================ 图 10 优先级矩阵
def fig_priority_matrix():
    """定性示意图：位置表示相对次序，不是实测值（图内已标注）。"""
    items = [
        ("轮胎 / 气压 → C_rr", 0.88, 0.62, 900, C_BAD),
        ("导流板 / 挂车匹配 → C_d·A", 0.80, 0.45, 780, C_BAD),
        ("换挡线 → 传动效率", 0.62, 0.80, 700, C_ACCENT),
        ("热管理阈值 → 附件功率", 0.55, 0.88, 620, C_ACCENT),
        ("怠速停机门限", 0.35, 0.92, 300, C_GOOD),
        ("回收进入/退出阈值", 0.70, 0.72, 560, C_ACCENT),
        ("预测性能量管理 PEM", 0.90, 0.30, 820, C_BAD),
        ("轻量化", 0.30, 0.18, 260, C_GREY),
    ]
    fig, ax = plt.subplots(figsize=(9.8, 6.0))
    ax.axvline(0.5, color="#DDDDDD", lw=1.2)
    ax.axhline(0.5, color="#DDDDDD", lw=1.2)
    for name, sx, room, gain, color in items:
        ax.scatter(sx, room, s=gain * 0.95, color=color, alpha=0.82,
                   edgecolors="white", linewidths=1.4, zorder=4)
        ax.text(sx, room - 0.062, name, ha="center", fontsize=9.0, color="#333333",
                linespacing=1.4)
    ax.text(0.975, 0.965, "优先动", ha="right", va="top", fontsize=11.5,
            weight="bold", color=C_BAD)
    ax.text(0.025, 0.965, "看长期 / 需试验资源", ha="left", va="top", fontsize=10.5,
            color=C_GREY)
    ax.text(0.975, 0.035, "先做标定验证", ha="right", va="bottom", fontsize=10.5,
            color=C_GREY)
    ax.set_xlabel("对能耗的相对灵敏度（定性次序）", fontsize=10.5)
    ax.set_ylabel("参数可改范围 / 工程可控性（定性次序）", fontsize=10.5)
    ax.set_xlim(0.1, 1.02)
    ax.set_ylim(0.05, 1.05)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("降耗方向优先级：灵敏度 × 可改范围 ÷ 代价（圆面积 = 预期收益量级）",
                 fontsize=11.8, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "注意 本图为定性示意图：位置表示相对次序，不是实测值。真实项目的坐标必须由式 (10) 用本车型的 "
            "S_p、可改范围与代价实测/评估后填入。",
            transform=ax.transAxes, ha="center", fontsize=9.3, color=C_BAD, linespacing=1.7)
    save(fig, "fig10_priority_matrix.png")


# ================================================================ 图 11 实测不可辨识
def fig_eta_measured():
    """条带图：比密度直方图更适合 n=4~56 的小样本，且重叠关系一眼可见。"""
    df = pd.read_csv(CSV, encoding="utf-8")
    order = ["combustion_degraded", "tire_pressure_low", "excessive_idling",
             "aggressive_driving", "normal"]
    zh = {"normal": "正常", "aggressive_driving": "激烈驾驶",
          "excessive_idling": "长时间怠速", "tire_pressure_low": "胎压不足",
          "combustion_degraded": "燃烧恶化"}
    colors = {"normal": C_MAIN, "aggressive_driving": C_GREY,
              "excessive_idling": "#8E44AD", "tire_pressure_low": C_BAD,
              "combustion_degraded": C_ACCENT}

    fig, ax = plt.subplots(figsize=(10.4, 6.0))
    ax.axvspan(0.3090, 0.3235, color=C_BAD, alpha=0.07, zorder=0)
    rng = np.random.default_rng(11)
    for row, g in enumerate(order):
        v = df.loc[df["injected_fault"] == g, "eta_engine_est"].to_numpy()
        ax.scatter(v, row + rng.uniform(-0.17, 0.17, v.size), s=34, color=colors[g],
                   alpha=0.85, edgecolors="white", linewidths=0.6, zorder=4)
        med = float(np.median(v))
        ax.plot([med, med], [row - 0.26, row + 0.26], color="#222222", lw=2.0, zorder=5)
        ax.text(0.3685, row, f"中位数 {med:.4f}", va="center", ha="right",
                fontsize=9.2, color=colors[g], weight="bold")
        ax.text(0.3055, row + 0.30, f"{zh[g]}（n={v.size}）", va="bottom", ha="left",
                fontsize=9.6, color=colors[g], weight="bold")

    ax.annotate("胎压不足 0.3122 与 燃烧恶化 0.3125\n两条中介几乎重合 → 相互不可辨识",
                xy=(0.3124, 0.55), xytext=(0.3242, 1.05), fontsize=9.8, color=C_BAD,
                weight="bold", linespacing=1.6,
                arrowprops=dict(arrowstyle="-|>", color=C_BAD, lw=1.5))
    ax.annotate("正常车次中位数 0.3467\n明显更高 → 能判「能效偏低」，\n不能判「是阻力还是效率」",
                xy=(0.3467, 4.0), xytext=(0.3335, 2.55), fontsize=9.6, color=C_MAIN,
                linespacing=1.6,
                arrowprops=dict(arrowstyle="-|>", color=C_MAIN, lw=1.4))

    ax.set_xlim(0.3050, 0.3700)
    ax.set_ylim(-0.6, 4.95)
    ax.set_yticks([])
    ax.set_xlabel("整体能效反标定量 η_engine_est（牵引机械功 / 可用于牵引的燃油化学能）", fontsize=9.8)
    ax.grid(axis="x", color="#EDEDED")
    ax.set_axisbelow(True)
    ax.set_title("实测不可辨识：胎压不足与燃烧恶化的 η 分布重合，而与正常车次可分",
                 fontsize=11.4, weight="bold", color=C_MAIN, pad=12)
    ax.text(0.5, CAP_Y,
            "真实产物 车队汇总.csv（80 车次）；两类故障之间的最优单阈值判别准确率仅 75.0%，随机基线 66.7%（L1/L2）\n"
            "→ 方案只给「两个候选假设」，再用独立观测量（胎压信号 / 滑行试验 / 台架）判别",
            transform=ax.transAxes, ha="center", fontsize=9.2, color=C_GREY, linespacing=1.7)
    save(fig, "fig11_eta_measured.png")


# ================================================================ 图 12 数据流
def fig_pipeline():
    fig, ax = plt.subplots(figsize=(14.2, 4.0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    nodes = [
        ("报文·台架\n路试记录", C_GREY),
        ("解码·对齐\n质量分", C_MAIN),
        ("账目\n里程三路径", C_MAIN),
        ("能量瀑布\n（拆）", "#11707A"),
        ("公平基线残差\n（比）", "#1B6E8C"),
        ("反标定·灵敏度\n（反·敏）", "#8E6C1F"),
        ("降耗方向清单\n（指）", C_ACCENT),
        ("A/B 验证\n回归基线（验）", C_GOOD),
    ]
    w, gap = 0.113, 0.0145
    for i, (text, color) in enumerate(nodes):
        x = 0.005 + i * (w + gap)
        box(ax, x, 0.44, w, 0.30, text, color, color, fs=8.4)
        if i < len(nodes) - 1:
            arrow(ax, (x + w + 0.002, 0.59), (x + w + gap - 0.002, 0.59), lw=1.5)

    ax.text(0.5, 0.28, "外部真值（加油 / 充电 / 台架 / 称重 / 滑行试验）在账目层介入，只用于验证，不参与建模",
            ha="center", fontsize=9.4, color=C_GREY)
    ax.text(0.5, 0.12, "AI 只在两个位置介入：① 公平基线（学物理模型解释不掉的残差）② 贡献分解（把残差翻译成可干预量）",
            ha="center", fontsize=9.6, color=C_MAIN, weight="bold")
    ax.text(0.5, 0.955, "分析链路：从原始报文到降耗方向", ha="center",
            fontsize=12.5, weight="bold", color=C_MAIN)
    save(fig, "fig12_pipeline.png")


def main() -> None:
    if not CSV.exists():
        raise SystemExit(f"缺少真实产物：{CSV}（请先在 prototype/ 下运行 python run_demo.py）")
    print("生成配图（全部基于真实产物与审计复算值）…")
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)   # 缺字体会在此处报错，避免出现方块
        fig_six_steps()
        fig_architecture()
        fig_sensitivity()
        fig_share_consistency()
        fig_identifiability_theory()
        fig_quantization_floor()
        fig_sample_size()
        fig_residual_distribution()
        fig_detection_performance()
        fig_priority_matrix()
        fig_eta_measured()
        fig_pipeline()
    print("完成。")


if __name__ == "__main__":
    main()
