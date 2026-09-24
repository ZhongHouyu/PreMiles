"""生成 DBC 文件： python build_dbc.py

DBC 由 src/dbc_spec.py 的规格渲染而来，保证"数据字典 → 解析器"口径一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.dbc_spec import render_dbc  # noqa: E402


def main() -> Path:
    out = ROOT / "config" / "trucks_demo.dbc"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dbc(), encoding="utf-8")

    # 立刻用 cantools 回读校验，确保 DBC 可被工业标准工具链解析
    import cantools

    db = cantools.database.load_file(str(out))
    n_sig = sum(len(m.signals) for m in db.messages)
    print(f"[build_dbc] 已生成 {out}")
    print(f"[build_dbc] cantools 回读成功: {len(db.messages)} 条报文 / {n_sig} 个信号")
    for m in db.messages:
        print(f"  - 0x{m.frame_id:08X} {m.name:<12} {m.length}B  "
              f"{m.cycle_time or 0:>4}ms  {len(m.signals)} signals")
    return out


if __name__ == "__main__":
    main()
