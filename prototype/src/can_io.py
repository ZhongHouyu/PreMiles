"""CAN 报文编解码与 candump 日志读写。

* 编码：按 DBC 规格做**矢量化**比特打包（numpy），用于仿真器快速生成海量报文。
* 解码：使用工业标准 `cantools` 从字节流还原物理值 —— 与分析真实车辆日志的路径完全一致。
* 日志格式：兼容 `candump -L` 的 `(timestamp) channel#IDDATA` 文本格式。
"""

from __future__ import annotations

import re
from pathlib import Path

import cantools
import numpy as np

# ----------------------------------------------------------------------
# DBC
# ----------------------------------------------------------------------
_DBC_CACHE: dict[str, cantools.database.Database] = {}


def load_dbc(path: str | Path) -> cantools.database.Database:
    path = str(path)
    if path not in _DBC_CACHE:
        _DBC_CACHE[path] = cantools.database.load_file(path)
    return _DBC_CACHE[path]


# ----------------------------------------------------------------------
# 矢量化编码
# ----------------------------------------------------------------------
def _sig_scale(sig) -> float:
    """兼容 cantools 版本差异：<39 用 factor，>=39 用 scale。"""
    return float(getattr(sig, "scale", None) if getattr(sig, "scale", None) is not None
                 else sig.factor)


def encode_message_vectorized(
    db: cantools.database.Database,
    msg_name: str,
    signal_values: dict[str, np.ndarray],
) -> np.ndarray:
    """把一个报文的多个信号矢量化为 (N, 8) uint8 字节矩阵。

    Intel(little-endian) 位序：整个 8 字节帧等价于一个 64 位小端整数，
    信号占据 [start, start+length) 位，可据此按位或合成。
    """
    msg = db.get_message_by_name(msg_name)
    n = 0
    for v in signal_values.values():
        n = max(n, len(v))
    words = np.zeros(n, dtype=np.uint64)

    for sig in msg.signals:
        if sig.name not in signal_values:
            continue
        phys = np.asarray(signal_values[sig.name], dtype=np.float64)
        if sig.minimum is not None:
            phys = np.maximum(phys, sig.minimum)
        if sig.maximum is not None:
            phys = np.minimum(phys, sig.maximum)
        raw = np.rint((phys - sig.offset) / _sig_scale(sig)).astype(np.int64)
        mask = (1 << sig.length) - 1
        raw &= mask                      # 负数自动落到补码表示
        words |= raw.astype(np.uint64) << np.uint64(int(sig.start))

    out = np.empty((n, 8), dtype=np.uint8)
    for b in range(8):
        out[:, b] = ((words >> np.uint64(8 * b)) & np.uint64(0xFF)).astype(np.uint8)
    return out


def encode_message_reference(
    db: cantools.database.Database,
    msg_name: str,
    signal_values: dict[str, float],
) -> bytes:
    """单帧参考编码（调用 cantools 官方编码器），用于校验矢量化实现的正确性。"""
    return db.encode_message(msg_name, signal_values)


# ----------------------------------------------------------------------
# candump 日志
# ----------------------------------------------------------------------
_L_FORMAT = re.compile(
    r"^\((?P<ts>\d+\.\d+)\)\s+(?P<ch>\S+?)#(?P<data>[0-9A-Fa-f]+)\s*$"
)
_CLASSIC_FORMAT = re.compile(
    r"^\((?P<ts>\d+\.\d+)\)\s+(?P<ch>\S+)\s+(?P<cid>[0-9A-Fa-f]+)\s+"
    r"\[(?P<dlc>\d+)\]\s*(?P<data>[0-9A-Fa-f ]*)$"
)


def write_candump(
    path: str | Path,
    timestamps: np.ndarray,
    can_ids: np.ndarray,
    data: np.ndarray,
    channel: str = "can0",
) -> Path:
    """写出 candump -L 格式日志。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = np.asarray(timestamps, dtype=np.float64)
    ids = np.asarray(can_ids, dtype=np.int64)
    dat = np.asarray(data, dtype=np.uint8)

    # 按时间排序（真实日志天然按时间到达）
    order = np.argsort(ts, kind="stable")
    ts, ids, dat = ts[order], ids[order], dat[order]

    hexdata = np.array(
        ["".join(f"{b:02X}" for b in row) for row in dat], dtype=object
    )
    lines = [
        f"({t:.6f}) {channel}#{i:08X}{d}\n"
        for t, i, d in zip(ts.tolist(), ids.tolist(), hexdata.tolist())
    ]
    with path.open("w", encoding="ascii", newline="\n") as fh:
        fh.writelines(lines)
    return path


def read_candump(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """读取 candump 日志 -> (timestamps, can_ids, data[N,8])。"""
    path = Path(path)
    ts_list: list[float] = []
    id_list: list[int] = []
    data_list: list[bytes] = []

    n_bad = 0
    with path.open("r", encoding="ascii", errors="replace") as fh:
        for line in fh:
            m = _L_FORMAT.match(line)
            if m:
                blob = m.group("data")
            else:
                m = _CLASSIC_FORMAT.match(line)
                if not m:
                    n_bad += 1
                    continue
                blob = m.group("data").replace(" ", "")
            ts_list.append(float(m.group("ts")))
            id_list.append(int(m.group("cid") if "cid" in m.groupdict()
                                else blob[:8], 16))
            payload = blob[8:] if "cid" not in m.groupdict() else blob
            if len(payload) % 2:
                payload = payload[:-1]
            data_list.append(bytes.fromhex(payload))

    n = len(ts_list)
    data = np.zeros((n, 8), dtype=np.uint8)
    for i, b in enumerate(data_list):
        data[i, : min(8, len(b))] = np.frombuffer(b[:8], dtype=np.uint8)
    return (np.asarray(ts_list, dtype=np.float64),
            np.asarray(id_list, dtype=np.int64),
            data)


def decode_log(
    db: cantools.database.Database,
    path: str | Path,
    wanted: set[str] | None = None,
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], dict]:
    """解码整份日志 -> {信号名: (时间戳, 物理值)} 以及解码统计。

    与解析真实车辆日志使用同一条代码路径（cantools.decode_message）。
    """
    ts, ids, data = read_candump(path)
    id_to_msg = {m.frame_id: m for m in db.messages}

    # 先按 CAN ID 分组，避免逐行查表
    groups: dict[int, list[int]] = {}
    unknown = 0
    for idx, cid in enumerate(ids.tolist()):
        if cid in id_to_msg:
            groups.setdefault(cid, []).append(idx)
        else:
            unknown += 1

    result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    n_frames_ok = 0
    n_frames_err = 0

    for cid, idxs in groups.items():
        msg = id_to_msg[cid]
        wanted_sigs = [s for s in msg.signals if wanted is None or s.name in wanted]
        if not wanted_sigs:
            n_frames_ok += len(idxs)
            continue
        arr = data[idxs]
        gts = ts[idxs]
        cols: dict[str, list[float]] = {s.name: [] for s in wanted_sigs}
        for k in range(arr.shape[0]):
            try:
                dec = msg.decode(arr[k].tobytes(), allow_truncated=False)
            except Exception:
                n_frames_err += 1
                continue
            for s in wanted_sigs:
                cols[s.name].append(dec[s.name])
            n_frames_ok += 1
        for name, vals in cols.items():
            result[name] = (gts, np.asarray(vals, dtype=np.float64))

    stats = {
        "frames_total": int(ids.size),
        "frames_decoded": n_frames_ok,
        "frames_failed": n_frames_err,
        "frames_unknown_id": unknown,
        "decode_success_rate": (n_frames_ok / ids.size) if ids.size else 0.0,
        "messages_present": len(groups),
    }
    return result, stats
