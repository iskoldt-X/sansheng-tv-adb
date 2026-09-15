"""adb 调用层：所有对电视的读写都经这里，便于用 ADB 环境变量换成回放用的假 adb 做测试。

设计原则：
- 每条命令都有超时；电视卡死时不能把 agent 一起拖死。
- 不吞错：返回码、stdout、stderr 原样带回，判断留给上层。
- 只暴露少量动词（shell / connect / devices / reboot / exec_out），别的都是它们的组合。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass


class AdbNotFound(RuntimeError):
    pass


def adb_binary() -> str:
    env = os.environ.get("ADB")
    if env:
        return env
    found = shutil.which("adb")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/.local/share/android-sdk/platform-tools/adb"),
        os.path.expanduser("~/AppData/Local/Android/Sdk/platform-tools/adb.exe"),
    ):
        if os.path.exists(candidate):
            return candidate
    raise AdbNotFound(
        "找不到 adb。macOS：brew install android-platform-tools；"
        "Windows：下载 Google platform-tools 并加入 PATH；Linux：apt install adb。"
    )


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return (self.out or "") + (("\n" + self.err) if self.err else "")


class Adb:
    def __init__(self, serial: str | None = None, timeout: int = 25):
        self.bin = adb_binary()
        self.serial = serial
        self.timeout = timeout

    # ---- 基础动词 -------------------------------------------------------
    def run(self, *args: str, timeout: int | None = None, binary: bool = False) -> Result:
        cmd = [self.bin]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout or self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Result(124, "", f"adb 超时（{timeout or self.timeout}s）：{' '.join(args)}")
        except FileNotFoundError as exc:
            raise AdbNotFound(str(exc)) from exc
        if binary:
            return Result(proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace"))  # type: ignore[arg-type]
        out = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "")
        err = proc.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
        return Result(proc.returncode, out, err)

    def shell(self, command: str, timeout: int | None = None) -> Result:
        return self.run("shell", command, timeout=timeout)

    def exec_out(self, command: str, timeout: int | None = None) -> bytes:
        res = self.run("exec-out", command, timeout=timeout or 60, binary=True)
        return res.out if isinstance(res.out, (bytes, bytearray)) else b""

    def connect(self, target: str) -> Result:
        return self.run("connect", target, timeout=15)

    def disconnect(self, target: str | None = None) -> Result:
        return self.run("disconnect", target) if target else self.run("disconnect")

    def devices(self) -> dict[str, str]:
        """返回 {serial: state}，state 取 device / unauthorized / offline。"""
        res = self.run("devices", timeout=15)
        table: dict[str, str] = {}
        for line in res.out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                table[parts[0]] = parts[1]
        return table

    def state_of(self, target: str) -> str:
        return self.devices().get(target, "absent")

    def reboot(self) -> Result:
        return self.run("reboot", timeout=15)

    # ---- 组合动词 -------------------------------------------------------
    def getprop(self, key: str | None = None) -> str:
        res = self.shell(f"getprop {key}" if key else "getprop")
        return res.out.strip()

    def getprops(self) -> dict[str, str]:
        props: dict[str, str] = {}
        for line in self.getprop().splitlines():
            line = line.strip()
            if line.startswith("[") and "]: [" in line:
                key, _, val = line[1:].partition("]: [")
                props[key] = val.rstrip("]")
        return props

    def wait_for(self, target: str, want: str = "device", timeout: int = 240, reconnect: bool = True) -> str:
        """等待设备进入某状态；硬上限，超时返回最后看到的状态。"""
        deadline = time.time() + timeout
        last = "absent"
        while time.time() < deadline:
            if reconnect and ":" in target:
                self.connect(target)
            last = self.state_of(target)
            if last == want:
                return last
            time.sleep(3)
        return last

    def boot_completed(self) -> bool:
        return self.getprop("sys.boot_completed").strip() == "1"
