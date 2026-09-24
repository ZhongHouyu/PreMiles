"""跟车报文信号定义（DBC 规格）—— 单一事实来源。

对应方案文档 3.2 节《关键信号清单》。

设计说明
--------
* CAN ID 采用 SAE J1939 风格 29 位扩展帧（0x18xxxxxx / 0x0Cxxxxxx）。
* 标准参数使用 J1939 真实 PGN/SPN（EEC1 / CCVS / LFE / VDHR / AMB）；
  TRK_xxx 为车载终端（T-Box）打包上传的私有扩展报文（PGN 0xFF00~0xFFFF）。
* 信号位序统一为 Intel / little-endian（DBC 写作 @1+ 或 @1-）。

信号清单同时充当"数据字典"，可直接给业务方与数据提供方对齐口径。
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 报文与信号定义
#   start  : DBC 起始位（Intel 位序，LSB 位置）
#   signed : True -> @1-，False -> @1+
#   factor / offset : 物理值 = 原始值 * factor + offset
# --------------------------------------------------------------------------
MESSAGES: list[dict] = [
    dict(
        name="EEC1",
        can_id=0x0CF00400,
        dlc=8,
        rate_hz=10.0,
        comment="电子发动机控制器1 (J1939 PGN 61444)",
        signals=[
            dict(name="EngineSpeed", start=24, length=16, signed=False,
                 factor=0.125, offset=0.0, minimum=0.0, maximum=8031.875,
                 unit="rpm", comment="SPN 190 发动机转速"),
            dict(name="ActualEnginePercentTorque", start=16, length=8, signed=False,
                 factor=1.0, offset=-125.0, minimum=-125.0, maximum=125.0,
                 unit="%", comment="SPN 513 实际发动机扭矩百分比"),
        ],
    ),
    dict(
        name="CCVS",
        can_id=0x18FEF100,
        dlc=8,
        rate_hz=10.0,
        comment="整车巡航/车速 (J1939 PGN 65265)",
        signals=[
            dict(name="WheelBasedVehicleSpeed", start=8, length=16, signed=False,
                 factor=1.0 / 256.0, offset=0.0, minimum=0.0, maximum=250.996,
                 unit="km/h", comment="SPN 84 基于车轮的车速"),
            dict(name="BrakeSwitch", start=44, length=2, signed=False,
                 factor=1.0, offset=0.0, minimum=0.0, maximum=3.0,
                 unit="", comment="SPN 597 制动开关 0=未踩 1=踩下"),
        ],
    ),
    dict(
        name="LFE",
        can_id=0x18FEEF00,
        dlc=8,
        rate_hz=10.0,
        comment="燃油经济性 (J1939 PGN 65257)",
        signals=[
            dict(name="EngineFuelRate", start=0, length=16, signed=False,
                 factor=0.05, offset=0.0, minimum=0.0, maximum=3212.75,
                 unit="L/h", comment="SPN 183 发动机瞬时燃油率（能耗主信号）"),
            dict(name="TotalFuelUsed", start=16, length=32, signed=False,
                 factor=0.5, offset=0.0, minimum=0.0, maximum=2147483647.5,
                 unit="L", comment="SPN 250 累计燃油消耗（对账基准，分辨率0.5L）"),
        ],
    ),
    dict(
        name="VDHR",
        can_id=0x18FEE800,
        dlc=8,
        rate_hz=1.0,
        comment="高分辨率整车里程 (J1939 PGN 65217)",
        signals=[
            dict(name="HighResolutionTotalVehicleDistance", start=0, length=32, signed=False,
                 factor=0.005, offset=0.0, minimum=0.0, maximum=21474836.475,
                 unit="km", comment="SPN 917 累计里程（分辨率5m）"),
        ],
    ),
    dict(
        name="AMB",
        can_id=0x18FEF500,
        dlc=8,
        rate_hz=0.2,
        comment="环境条件 (J1939 PGN 65269)",
        signals=[
            dict(name="AmbientAirTemperature", start=0, length=16, signed=False,
                 factor=0.03125, offset=-273.0, minimum=-273.0, maximum=1735.0,
                 unit="degC", comment="SPN 171 环境温度"),
        ],
    ),
    # ---------------- T-Box 私有扩展报文 ----------------
    dict(
        name="TRK_LOAD1",
        can_id=0x18FF1000,
        dlc=8,
        rate_hz=1.0,
        comment="整车载荷（T-Box 私有 / 轴荷传感器）",
        signals=[
            dict(name="TotalVehicleMass", start=0, length=16, signed=False,
                 factor=1.0, offset=0.0, minimum=0.0, maximum=65535.0,
                 unit="kg", comment="整车总质量（能耗第一敏感因子）"),
            dict(name="AxleLoadFront", start=16, length=16, signed=False,
                 factor=1.0, offset=0.0, minimum=0.0, maximum=65535.0,
                 unit="kg", comment="前轴荷"),
        ],
    ),
    dict(
        name="TRK_ENV1",
        can_id=0x18FF2000,
        dlc=8,
        rate_hz=1.0,
        comment="道路环境（T-Box 私有 / IMU 融合坡度）",
        signals=[
            dict(name="RoadGrade", start=0, length=16, signed=True,
                 factor=0.001, offset=0.0, minimum=-32.768, maximum=32.767,
                 unit="%", comment="道路坡度（IMU+GNSS 融合输出）"),
            dict(name="WindSpeed", start=16, length=8, signed=False,
                 factor=1.0, offset=0.0, minimum=0.0, maximum=255.0,
                 unit="km/h", comment="等效风速（无传感器时置0）"),
        ],
    ),
    dict(
        name="TRK_GNSS1",
        can_id=0x18FF3000,
        dlc=8,
        rate_hz=1.0,
        comment="GNSS 位置（T-Box 私有）",
        signals=[
            dict(name="Latitude", start=0, length=32, signed=True,
                 factor=1e-07, offset=0.0, minimum=-90.0, maximum=90.0,
                 unit="deg", comment="纬度"),
            dict(name="Longitude", start=32, length=32, signed=True,
                 factor=1e-07, offset=0.0, minimum=-180.0, maximum=180.0,
                 unit="deg", comment="经度"),
        ],
    ),
    dict(
        name="TRK_GNSS2",
        can_id=0x18FF4000,
        dlc=8,
        rate_hz=1.0,
        comment="GNSS 高程与速度（T-Box 私有）",
        signals=[
            dict(name="GNSSAltitude", start=0, length=16, signed=False,
                 factor=0.1, offset=-500.0, minimum=-500.0, maximum=6053.5,
                 unit="m", comment="海拔高程（坡度估计来源之一）"),
            dict(name="GNSSSpeed", start=16, length=16, signed=False,
                 factor=0.01, offset=0.0, minimum=0.0, maximum=655.35,
                 unit="km/h", comment="GNSS 对地速度（里程第三方校验）"),
            dict(name="GNSSHeading", start=32, length=16, signed=False,
                 factor=0.0078125, offset=0.0, minimum=0.0, maximum=511.99,
                 unit="deg", comment="航向角"),
            dict(name="GNSSSatellites", start=48, length=8, signed=False,
                 factor=1.0, offset=0.0, minimum=0.0, maximum=255.0,
                 unit="", comment="可见卫星数（用于质量评分）"),
        ],
    ),
    dict(
        name="TRK_TPMS1",
        can_id=0x18FF6000,
        dlc=8,
        rate_hz=0.5,
        comment="胎压监测（T-Box 私有 / TPMS）",
        signals=[
            dict(name="TirePressureFront", start=0, length=8, signed=False,
                 factor=5.0, offset=0.0, minimum=0.0, maximum=1275.0,
                 unit="kPa", comment="前轴平均胎压（标准值约 830 kPa）"),
            dict(name="TirePressureRear", start=8, length=8, signed=False,
                 factor=5.0, offset=0.0, minimum=0.0, maximum=1275.0,
                 unit="kPa", comment="后轴平均胎压"),
        ],
    ),
    dict(
        name="TRK_BMS1",
        can_id=0x18FF5000,
        dlc=8,
        rate_hz=10.0,
        comment="动力电池与电驱（新能源车型专用）",
        signals=[
            dict(name="PackVoltage", start=0, length=16, signed=False,
                 factor=0.1, offset=0.0, minimum=0.0, maximum=6553.5,
                 unit="V", comment="电池包总电压"),
            dict(name="PackCurrent", start=16, length=16, signed=True,
                 factor=0.1, offset=0.0, minimum=-3276.8, maximum=3276.7,
                 unit="A", comment="电池包电流（放电为正、回收为负）"),
            dict(name="StateOfCharge", start=32, length=8, signed=False,
                 factor=0.4, offset=0.0, minimum=0.0, maximum=100.0,
                 unit="%", comment="SOC"),
            dict(name="MotorTorque", start=40, length=16, signed=True,
                 factor=1.0, offset=0.0, minimum=-32768.0, maximum=32767.0,
                 unit="Nm", comment="电机扭矩（驱动为正、回收为负）"),
            dict(name="MotorSpeedRpm100", start=56, length=8, signed=False,
                 factor=100.0, offset=0.0, minimum=0.0, maximum=25500.0,
                 unit="rpm", comment="电机转速（分辨率100rpm）"),
        ],
    ),
]

# 信号名 -> 报文名 反查表
SIGNAL_TO_MESSAGE: dict[str, str] = {
    s["name"]: m["name"] for m in MESSAGES for s in m["signals"]
}

# 信号名 -> 规格
SIGNAL_SPEC: dict[str, dict] = {
    s["name"]: s for m in MESSAGES for s in m["signals"]
}

# 报文名 -> CAN ID
MESSAGE_ID: dict[str, int] = {m["name"]: m["can_id"] for m in MESSAGES}

# 分析所需的核心信号（缺失时的降级策略见方案文档 4.6）
CORE_SIGNALS = [
    "WheelBasedVehicleSpeed",
    "EngineSpeed",
    "ActualEnginePercentTorque",
    "EngineFuelRate",
    "TotalFuelUsed",
    "HighResolutionTotalVehicleDistance",
]


def render_dbc() -> str:
    """把规格渲染为标准 DBC 文本。

    DBC 中用最高位标记 29 位扩展帧：BO_ 的 ID = can_id | 0x80000000。
    """
    ext = lambda cid: cid | 0x80000000  # noqa: E731
    lines: list[str] = []
    lines.append('VERSION ""')
    lines.append("")
    lines.append("NS_ :")
    for token in (
        "NS_DESC_", "CM_", "BA_DEF_", "BA_", "VAL_", "CAT_DEF_", "CAT_", "FILTER",
        "BA_DEF_DEF_", "EV_DATA_", "ENVVAR_DATA_", "SGTYPE_", "SGTYPE_VAL_",
        "BA_DEF_SGTYPE_", "BA_SGTYPE_", "SIG_TYPE_REF_", "VAL_TABLE_",
        "SIG_GROUP_", "SIG_VALTYPE_", "SIGTYPE_VALTYPE_", "BO_TX_BU_",
        "BA_DEF_REL_", "BA_REL_", "BA_DEF_DEF_REL_", "BU_SG_REL_", "BU_EV_REL_",
        "BU_BO_REL_", "SG_MUL_VAL_",
    ):
        lines.append("\t" + token)
    lines.append("")
    lines.append("BS_:")
    lines.append("")
    lines.append("BU_: TBOX ECU")
    lines.append("")

    for m in MESSAGES:
        lines.append(f'BO_ {ext(m["can_id"])} {m["name"]}: {m["dlc"]} TBOX')
        for s in m["signals"]:
            sign = "-" if s["signed"] else "+"
            lines.append(
                f' SG_ {s["name"]} : {s["start"]}|{s["length"]}@1{sign} '
                f'({s["factor"]:g},{s["offset"]:g}) '
                f'[{s["minimum"]:g}|{s["maximum"]:g}] '
                f'"{s["unit"]}" ECU'
            )
        lines.append("")

    # 报文注释
    for m in MESSAGES:
        lines.append(f'CM_ BO_ {ext(m["can_id"])} "{m["comment"]}";')
    # 信号注释
    for m in MESSAGES:
        for s in m["signals"]:
            lines.append(f'CM_ SG_ {ext(m["can_id"])} {s["name"]} "{s["comment"]}";')
    lines.append("")

    # 循环发送周期（CANdb 扩展属性），便于阅读
    lines.append('BA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;')
    lines.append('BA_DEF_DEF_ "GenMsgCycleTime" 0;')
    for m in MESSAGES:
        cycle = int(round(1000.0 / m["rate_hz"]))
        lines.append(f'BA_ "GenMsgCycleTime" BO_ {ext(m["can_id"])} {cycle};')
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(render_dbc())
