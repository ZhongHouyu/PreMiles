"""AI 建模层：能耗基线预测、残差异常检测、归因、车况衰减。

对应方案文档 4.7 节。

设计要点
--------
* **物理模型给"应该多少"，机器学习学"残差"** —— 样本需求低、结论可解释。
* 基线模型同时是**公平比较器**：把"山区重载"和"城配空载"拉到同一起跑线。
* 异常检测双轨：残差 z-score（统计可解释） + IsolationForest（发现未知模式）。
* 归因采用**单参考点贡献分解**（SHAP 的简化形式，无需额外依赖），
  生产环境建议直接换 `shap.TreeExplainer`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest
from sklearn.model_selection import KFold, cross_val_predict

RANDOM_STATE = 20241013


@dataclass
class BaselineModel:
    feature_names: list[str]
    model: HistGradientBoostingRegressor
    y_true: np.ndarray
    y_pred_oof: np.ndarray          # 交叉验证的样本外预测（避免自证）
    residual: np.ndarray
    residual_pct: np.ndarray
    metrics: dict = field(default_factory=dict)
    reference: dict = field(default_factory=dict)   # 单参考点归因的参考向量

    def explain(self, x: dict, top_k: int = 5, max_features: int = 14) -> list[dict]:
        """单参考点归因：把特征逐个替换为参考值，观察预测变化量。

        这是 SHAP 的高效近似（参考点取车队中位数），
        结论形式为"该车次比基线高 X L/100km，其中 Y 来自载荷、Z 来自急加速…"。
        """
        base_x = np.array([[x[n] for n in self.feature_names]])
        base_pred = float(self.model.predict(base_x)[0])

        # 只对与模型的强相关特征做归因，控制计算量
        imp = self.metrics.get("permutation_importance", {})
        key_feats = sorted(imp, key=lambda k: -imp[k])[:max_features] if imp else self.feature_names

        contribs = []
        for name in key_feats:
            if name not in x:
                continue
            probe = dict(x)
            probe[name] = self.reference[name]
            p = float(self.model.predict(
                np.array([[probe[n] for n in self.feature_names]]))[0])
            contribs.append(dict(feature=name, value=x[name],
                                 reference=self.reference[name],
                                 contribution=base_pred - p))
        contribs.sort(key=lambda d: -abs(d["contribution"]))
        return contribs[:top_k]


def train_baseline(feat_df: pd.DataFrame, target: str,
                   drop_cols: set[str] | None = None,
                   n_splits: int = 5) -> BaselineModel:
    """训练能耗基线模型，并给出**样本外**残差（避免用训练误差自证）。"""
    drop = set(drop_cols or set()) | {target}
    feature_names = [c for c in feat_df.columns if c not in drop]
    X = feat_df[feature_names].to_numpy(dtype=float)
    y = feat_df[target].to_numpy(dtype=float)

    model = HistGradientBoostingRegressor(
        max_depth=4, max_iter=400, learning_rate=0.05,
        min_samples_leaf=8, l2_regularization=1.0,
        random_state=RANDOM_STATE,
    )

    n_splits = int(min(n_splits, max(2, y.size // 3)))
    cv = KFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_STATE)
    y_pred = cross_val_predict(model, X, y, cv=cv)
    model.fit(X, y)

    resid = y - y_pred
    resid_pct = resid / np.maximum(y, 1e-9) * 100.0

    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    metrics = dict(
        n_samples=int(y.size), n_features=len(feature_names),
        r2_oof=1.0 - ss_res / max(ss_tot, 1e-9),
        mae_oof=float(np.mean(np.abs(resid))),
        rmse_oof=float(np.sqrt(np.mean(resid ** 2))),
        mape_oof=float(np.mean(np.abs(resid_pct))),
        target_mean=float(y.mean()),
    )
    metrics["permutation_importance"] = permutation_importance(model, X, y, feature_names)

    reference = {n: float(np.median(feat_df[n])) for n in feature_names}
    return BaselineModel(feature_names, model, y, y_pred, resid, resid_pct,
                         metrics, reference)


def permutation_importance(model, X: np.ndarray, y: np.ndarray,
                           names: list[str], n_repeat: int = 3) -> dict[str, float]:
    """置换重要度（不依赖 shap 的全局归因）。"""
    rng = np.random.default_rng(RANDOM_STATE)
    base = float(np.mean(np.abs(y - model.predict(X))))
    out: dict[str, float] = {}
    for j, name in enumerate(names):
        deltas = []
        for _ in range(n_repeat):
            Xp = X.copy()
            rng.shuffle(Xp[:, j])
            deltas.append(float(np.mean(np.abs(y - model.predict(Xp)))) - base)
        out[name] = float(np.mean(deltas))
    return out


def fit_anomaly_detector(feat_df: pd.DataFrame, residual_pct: np.ndarray,
                         contamination: float = 0.12) -> tuple[IsolationForest, np.ndarray, list[str]]:
    """在"片段特征 + 物理残差"空间上做无监督异常检测。"""
    cols = [c for c in feat_df.columns
            if feat_df[c].dtype != object]
    X = feat_df[cols].copy()
    X["residual_pct"] = residual_pct
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    names = list(X.columns)

    iso = IsolationForest(n_estimators=300, contamination=contamination,
                          random_state=RANDOM_STATE)
    iso.fit(X.to_numpy(dtype=float))
    # score_samples: 越小越异常；取负使"越大越异常"
    score = -iso.score_samples(X.to_numpy(dtype=float))
    return iso, score, names


def cusum_changepoint(residual_pct: np.ndarray, slack: float = 3.0,
                      threshold: float = 12.0) -> dict:
    """CUSUM 变点检测：判断某车的能耗残差是否发生持续上移（车况劣化）。"""
    pos = np.zeros(residual_pct.size)
    s = 0.0
    for i, r in enumerate(residual_pct):
        s = max(0.0, s + r - slack)
        pos[i] = s
    idx = np.flatnonzero(pos > threshold)
    return dict(
        cusum=pos,
        alarm=bool(idx.size > 0),
        first_alarm_index=int(idx[0]) if idx.size else None,
        max_cusum=float(pos.max()) if pos.size else 0.0,
        threshold=threshold,
    )


def build_cohort_stats(feat_df: pd.DataFrame, residual_pct: np.ndarray) -> dict:
    """车队同期基线统计量（用于公平比较与驾驶评分基准）。"""
    def q(a, p):
        return float(np.percentile(a, p))

    acc = feat_df.get("harsh_accel_per_100km", pd.Series([0.0])).to_numpy(dtype=float)
    brk = feat_df.get("harsh_brake_per_100km", pd.Series([0.0])).to_numpy(dtype=float)
    idle = feat_df.get("idle_time_pct", pd.Series([0.0])).to_numpy(dtype=float)
    cruise = feat_df.get("cruise_time_pct", pd.Series([0.0])).to_numpy(dtype=float)
    return dict(
        residual_p50=q(residual_pct, 50), residual_iqr=max(q(residual_pct, 75) - q(residual_pct, 25), 1.0),
        accel_p50=q(acc, 50), accel_iqr=max(q(acc, 75) - q(acc, 25), 0.5),
        brake_p50=q(brk, 50), brake_iqr=max(q(brk, 75) - q(brk, 25), 0.5),
        idle_p50=q(idle, 50), idle_iqr=max(q(idle, 75) - q(idle, 25), 1.0),
        noncruise_p50=q(100.0 - cruise, 50),
        noncruise_iqr=max(q(100.0 - cruise, 75) - q(100.0 - cruise, 25), 5.0),
    )


def detect_faults(residual_total: np.ndarray, anomaly_score: np.ndarray,
                  features: pd.DataFrame,
                  z_thresh: float = 1.8, iso_quantile: float = 0.88,
                  min_residual_pct: float = 1.5) -> list[dict]:
    """把统计信号翻译成业务可读的疑似异常，并判定责任归属。

    三级判据（任一命中即告警）：
      ① 物理规则判据：某一能耗分项/行为指标显著高于同类
         —— 最可解释，小样本下最稳健，**不受残差大小限制**（长怠速即使总能耗
            被基线部分吸收，规则依然能抓到）
      ② 统计判据：残差 z 分数显著且为正
      ③ 无监督判据：IsolationForest 离群且残差为正

    只对正残差告警 —— 比基线省油不是异常。

    责任归属按**证据类型**判定（而非再训一个模型），对应业务问题
    "这台车费油，是车的问题还是司机的问题"：
      驾驶行为类证据：怠速、急加速、急减速、PKE、经济车速占比、风阻(车速所致)
      车辆状况类证据：滚动阻力异常、无法解释的系统性偏高
    """
    z = (residual_total - np.median(residual_total)) / max(np.std(residual_total), 1e-9)
    iso_cut = float(np.quantile(anomaly_score, iso_quantile))

    def rule_threshold(col: str, ratio: float, high: bool = True,
                       default: float = 0.0) -> float:
        """离群阈值：同时要求「统计离群」与「物理倍数」才算异常。

        high=True  -> max(P90, ratio*中位数)   —— 越大越异常
        high=False -> max(ratio*中位数, P05)   —— 越小越异常

        低侧刻意**不**用 P10：当异常样本恰好占 10% 时，P10 就落在异常样本身上，
        阈值会等于异常值本身，规则永远无法触发（胎压规则曾因此完全失效）。
        """
        if col not in features:
            return default
        a = features[col].to_numpy(dtype=float)
        a = a[np.isfinite(a)]
        if a.size == 0:
            return default
        med = float(np.median(a))
        if high:
            return max(float(np.percentile(a, 90)), med * ratio)
        return max(med * ratio, float(np.percentile(a, 5)))

    TH = {
        "idle": rule_threshold("idle_s_per_100km", 1.8),
        "accel": rule_threshold("harsh_accel_per_100km", 1.8),
        "brake": rule_threshold("harsh_brake_per_100km", 1.8),
        "pke": rule_threshold("pke_kj_per_km", 1.6),
        "cruise_lo": rule_threshold("cruise_time_pct", 0.65, high=False),
        "aero": rule_threshold("aero_kwh_per_km", 1.12),
        "speed": rule_threshold("avg_speed_moving_kph", 1.08),
        "eta_lo": rule_threshold("eta_engine_est", 0.93, high=False),
        "tp_lo": rule_threshold("tire_pressure_kpa", 0.88, high=False),
    }
    med_of = lambda c, d=0.0: float(features[c].median()) if c in features else d  # noqa: E731

    findings: list[dict] = []
    med_idle = med_of("idle_s_per_100km", 0.0)
    for i in range(len(residual_total)):
        excess = float(residual_total[i])
        row = features.iloc[i]
        driving: list[str] = []
        vehicle: list[str] = []
        unexplained: list[str] = []
        strong = False          # 是否具备"独立于总能耗"的强证据

        # ---- ① 物理规则判据 ----
        if row.get("idle_s_per_100km", 0.0) > TH["idle"]:
            driving.append(f"每百公里怠速 {row['idle_s_per_100km']:.0f} 秒"
                           f"（车队中位数 {med_idle:.0f} 秒，异常阈值 {TH['idle']:.0f} 秒），"
                           f"长时间怠速不熄火")
            strong |= row["idle_s_per_100km"] > max(2.5 * med_idle, 1.0)
        if row.get("harsh_accel_per_100km", 0.0) > TH["accel"]:
            driving.append(f"急加速 {row['harsh_accel_per_100km']:.1f} 次/100km"
                           f"（中位数 {med_of('harsh_accel_per_100km'):.1f}），起步过猛")
        if row.get("harsh_brake_per_100km", 0.0) > TH["brake"]:
            driving.append(f"急减速 {row['harsh_brake_per_100km']:.1f} 次/100km"
                           f"（中位数 {med_of('harsh_brake_per_100km'):.1f}），"
                           f"预见性驾驶不足，制动能量白白耗散")
        if row.get("pke_kj_per_km", 0.0) > TH["pke"]:
            driving.append(f"正动能需求 PKE {row['pke_kj_per_km']:.3f}"
                           f"（中位数 {med_of('pke_kj_per_km'):.3f}），整体驾驶激烈度偏高")
        if row.get("cruise_time_pct", 0.0) < TH["cruise_lo"]:
            driving.append(f"经济车速占比偏低（{row['cruise_time_pct']:.1f}% vs "
                           f"中位数 {med_of('cruise_time_pct'):.1f}%），"
                           f"发动机长期工作在低效区")
        # 超速：卡车激烈驾驶的主要能耗代价。空气阻力能量每公里 = ½ρC_dA·v²，
        # 与车速平方成正比，因此"风阻能耗/km"本身就是车速水平的直接度量。
        if (row.get("avg_speed_moving_kph", 0.0) > TH["speed"]
                or row.get("aero_kwh_per_km", 0.0) > TH["aero"]):
            delta = row.get("aero_kwh_per_km", 0.0) / max(med_of("aero_kwh_per_km"), 1e-9) - 1.0
            driving.append(
                f"行驶平均车速 {row.get('avg_speed_moving_kph', 0.0):.1f} km/h 高于同类"
                f"（中位数 {med_of('avg_speed_moving_kph'):.1f} km/h），"
                f"空气阻力能耗比同类高 {delta:.0%} —— 风阻与车速平方成正比，超速代价被放大")
        # 整体能效反标定：牵引侧机械功 / 可用于牵引的燃油化学能（分子分母均剔除怠速）。
        # 重要：仅凭油耗**无法**区分"行驶阻力增大"与"动力系统效率下降" ——
        # 两者都表现为整体能效下降。因此这里同时给出两个假设，
        # 再由胎压信号（唯一独立观测量）做判别，而不是武断地归因。
        eta_low = row.get("eta_engine_est", 1.0) < TH["eta_lo"]
        tp_low = (row.get("tire_pressure_kpa") is not None
                  and np.isfinite(row.get("tire_pressure_kpa", np.nan))
                  and row["tire_pressure_kpa"] < TH["tp_lo"])
        if eta_low:
            base = (f"整体能效（牵引机械功/燃油化学能）{row['eta_engine_est']:.1%} "
                    f"低于同类中位数 {med_of('eta_engine_est', 0.3):.1%}，"
                    f"已剔除怠速与工况结构影响")
            if tp_low:
                vehicle.append(
                    f"胎压 {row['tire_pressure_kpa']:.0f} kPa 显著低于标准 "
                    f"{med_of('tire_pressure_kpa', 830.0):.0f} kPa → {base}，"
                    f"判定为行驶阻力增大（胎压不足），建议补气后复测")
            else:
                vehicle.append(
                    f"{base}；两个候选原因需现场区分 —— "
                    f"① 行驶阻力增大（刹车拖滞/轴承/四轮定位）"
                    f"② 动力系统效率下降（燃烧恶化/进气堵塞/喷油器衰减）")
            strong = True
        elif tp_low:
            vehicle.append(
                f"胎压 {row['tire_pressure_kpa']:.0f} kPa 显著低于标准 "
                f"{med_of('tire_pressure_kpa', 830.0):.0f} kPa，"
                f"滚动阻力将升高，建议补气并复测能耗")
            strong = True

        stat_hit = bool(z[i] >= z_thresh and excess > min_residual_pct)
        iso_hit = bool(anomaly_score[i] >= iso_cut and excess > min_residual_pct)
        rule_hit = bool(driving or vehicle)

        # 仅凭行为规则命中、且总能耗并未超标、证据又不强时，不告警 ——
        # 否则正常车次里随机出现的几次急加速就会造成大量误报。
        if not vehicle and not stat_hit and not iso_hit and not strong:
            continue
        if not rule_hit and excess <= 0:
            continue
        if not (driving or vehicle):
            # 没有物理解释时**不下结论** —— 把"查不出原因"直接归为车辆故障
            # 会系统性冤枉车辆，也让责任归属失去可信度。
            unexplained.append("能耗显著高于同类应达水平，但未定位到单一物理原因，"
                               "建议人工复核数据质量与车辆技术状况")

        if driving and vehicle:
            primary = "驾驶行为与车辆状况共同作用"
        elif driving:
            primary = "驾驶行为主导"
        elif vehicle:
            primary = "车辆状况主导"
        else:
            primary = "待人工判定"

        evidence = [k for k, v in (("物理规则", rule_hit), ("统计残差", stat_hit),
                                   ("无监督离群", iso_hit)) if v]
        findings.append(dict(
            index=int(i),
            residual_pct=excess,
            z_score=float(z[i]),
            anomaly_score=float(anomaly_score[i]),
            severity="高" if (z[i] >= 3.0 or len(driving) + len(vehicle) >= 2) else "中",
            primary=primary,
            evidence=evidence,
            driving_causes=driving,
            vehicle_causes=vehicle,
            unexplained=unexplained,
            causes=driving + vehicle + unexplained,
        ))

    findings.sort(key=lambda d: -d["residual_pct"])
    return findings


def peer_residual(feat_df: pd.DataFrame, target: np.ndarray,
                  match_cols: tuple[str, ...] = ("payload_t", "distance_km",
                                                 "grade_abs_mean"),
                  k: int = 9) -> np.ndarray:
    """近邻同类对比残差 —— 方案中的"公平比较器"。

    对每个车次，在**同载荷、同里程结构、同地形**的近邻中找到 K 个同类车次，
    以其能耗中位数为基准。这条路径不依赖任何模型，可直接向司机解释
    "你和跑同样活儿的同事比，多烧了多少油"。
    """
    cols = [c for c in match_cols if c in feat_df.columns]
    if not cols:
        return target - np.median(target)
    X = feat_df[cols].to_numpy(dtype=float)
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0)
    sd[sd < 1e-9] = 1.0
    Z = (X - mu) / sd

    out = np.zeros(len(target))
    for i in range(len(target)):
        d = np.linalg.norm(Z - Z[i], axis=1)
        d[i] = np.inf                       # 排除自己
        idx = np.argsort(d)[:max(3, min(k, len(target) - 1))]
        base = float(np.median(target[idx]))
        out[i] = (target[i] - base) / max(abs(base), 1e-9) * 100.0
    return out
