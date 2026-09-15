"""一次清理 = 一个工作目录（默认 ~/tv-adb/<型号>-<时间>/）。

目录里的每个文件都是给人看的证据，不是给程序看的缓存：
  device.json    电视身份（getprop 摘要）与资格判断
  backup/        四类备份 + sha256 清单（未备份不许停用）
  state.json     命令能力、每批停了什么、用了哪条命令
  rollback.sh    每停一个包立刻追加一行还原命令；任何时候 sh rollback.sh 全部还原
  DEBLOAT-LOG.md 人读日志：做了什么、为什么、怎么撤
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

HOME = Path(os.environ.get("TVADB_HOME", os.path.expanduser("~/tv-adb")))


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "tv"


class Session:
    def __init__(self, workdir: Path):
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "backup").mkdir(exist_ok=True)

    # ---- 创建 / 定位 ----------------------------------------------------
    @classmethod
    def create(cls, model_hint: str = "tv") -> "Session":
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
        return cls(HOME / f"{safe_name(model_hint)}-{stamp}")

    @classmethod
    def latest(cls) -> "Session | None":
        if not HOME.exists():
            return None
        dirs = sorted([p for p in HOME.iterdir() if p.is_dir() and (p / "state.json").exists()])
        return cls(dirs[-1]) if dirs else None

    @classmethod
    def open(cls, path: str | None) -> "Session":
        if path:
            return cls(Path(path).expanduser())
        found = cls.latest()
        if not found:
            raise SystemExit("没有找到进行中的清理目录；先跑 probe。也可以用 --workdir 指定。")
        return found

    # ---- JSON 读写 -------------------------------------------------------
    def _read(self, name: str, default):
        p = self.dir / name
        if not p.exists():
            return default
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def _write(self, name: str, obj) -> None:
        tmp = self.dir / (name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.dir / name)

    @property
    def device(self) -> dict:
        return self._read("device.json", {})

    @device.setter
    def device(self, obj: dict) -> None:
        self._write("device.json", obj)

    @property
    def state(self) -> dict:
        return self._read(
            "state.json",
            {
                "created": now_iso(),
                "target": None,
                "caps": {},
                "batches": [],
                "actions": [],
                "temp_disabled": [],
                "phase": "created",
            },
        )

    @state.setter
    def state(self, obj: dict) -> None:
        obj["updated"] = now_iso()
        self._write("state.json", obj)

    @property
    def plan(self) -> dict:
        return self._read("plan.json", {})

    @plan.setter
    def plan(self, obj: dict) -> None:
        self._write("plan.json", obj)

    # ---- 回滚脚本与日志 ---------------------------------------------------
    @property
    def rollback(self) -> Path:
        p = self.dir / "rollback.sh"
        if not p.exists():
            p.write_text(
                "#!/bin/sh\n# 还原脚本：每停一个包追加一行。用法：先 adb connect <电视IP>:5555，再 sh rollback.sh\n"
                "# 想只还原某一个包，复制对应那一行单独执行即可。\nset -x\n",
                encoding="utf-8",
            )
        return p

    def add_rollback(self, lines: list[str], comment: str = "") -> None:
        with open(self.rollback, "a", encoding="utf-8") as fh:
            if comment:
                fh.write(f"# {comment}\n")
            for line in lines:
                fh.write(line.rstrip("\n") + "\n")

    def log(self, text: str) -> None:
        p = self.dir / "DEBLOAT-LOG.md"
        if not p.exists():
            p.write_text("# 电视清理记录\n\n", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"- {now_iso()} {text}\n")

    def note_action(self, **kw) -> None:
        st = self.state
        kw.setdefault("at", now_iso())
        st["actions"].append(kw)
        self.state = st
