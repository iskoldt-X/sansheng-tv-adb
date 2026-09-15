#!/usr/bin/env python3
"""回放用的假 adb：用两台真机（2017 海信 VIDAA、2013 夏普×联想）的真实输出做 fixture，
再加一点状态机，让 disable / hide / uninstall / reboot 有和真机一致的反应。

用法：ADB=tests/mock_adb.py MOCK_TV_PROFILE=hisense2017 python3 scripts/tvadb.py probe 192.168.1.34
状态放在 $MOCK_TV_STATE（默认 /tmp/mock_tv_state.json），删掉即复位。

各 profile 的行为差异（全部来自实测或多源证据）：
  hisense2017   disable-user → SecurityException；hide 可用；第一批停完后的第一次真重启桌面黑屏（一次性）
  lenovo404     Android 4.0.4：pm disable → SecurityException；pm 无 hide；无授权弹窗；只有 /data/app 包可卸
  generic9      干净的 Android 9：disable-user / hide / uninstall --user 0 都可用
  coocaa_revert 酷开式：disable-user 报权限；hide 可用但重启后被系统恢复；uninstall --user 0 可用
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = os.environ.get("MOCK_TV_PROFILE", "hisense2017")
STATE_PATH = Path(os.environ.get("MOCK_TV_STATE", "/tmp/mock_tv_state.json"))
FIXTURE = {"hisense2017": "hisense2017", "lenovo404": "lenovo404", "generic9": "hisense2017", "coocaa_revert": "hisense2017"}[PROFILE]
FX = HERE / "fixtures" / FIXTURE

SEC_EXC = "Error: java.lang.SecurityException: Permission Denial: attempt to change component state from pid=7284, uid=2000, package uid=10016"


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    pk = {}
    for line in (FX / "packages_all.txt").read_text().splitlines():
        if line.startswith("package:"):
            path, _, name = line[8:].rpartition("=")
            pk[name] = path
    disabled = [l[8:] for l in (FX / "packages_disabled.txt").read_text().splitlines() if l.startswith("package:")]
    st = {
        "packages": pk, "disabled": disabled, "hidden": [], "uninstalled": [],
        "auth_polls": 0, "authorized": PROFILE == "lenovo404",  # 老联想无授权弹窗
        "reboots": 0, "black_once_pending": False, "black_now": False,
        "stopped_ads": False,
    }
    save_state(st)
    return st


def save_state(st: dict) -> None:
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1))


def visible(st: dict) -> list[str]:
    return sorted(p for p in st["packages"] if p not in st["hidden"] and p not in st["uninstalled"])


def getprop_text(st: dict) -> str:
    text = (FX / "getprop.txt").read_text()
    if PROFILE == "generic9":
        text = re.sub(r"\[ro.build.version.release\]: \[[^\]]*\]", "[ro.build.version.release]: [9]", text)
        text = re.sub(r"\[ro.build.version.sdk\]: \[[^\]]*\]", "[ro.build.version.sdk]: [28]", text)
        text = re.sub(r"\[ro.product.brand\]: \[[^\]]*\]", "[ro.product.brand]: [GenericTV]", text)
    if PROFILE == "coocaa_revert":
        text = re.sub(r"\[ro.product.brand\]: \[[^\]]*\]", "[ro.product.brand]: [Skyworth]", text)
        text = re.sub(r"\[ro.product.model\]: \[[^\]]*\]", "[ro.product.model]: [5S58_K5C]", text)
        text = re.sub(r"\[ro.build.version.release\]: \[[^\]]*\]", "[ro.build.version.release]: [6.0]", text)
        text = re.sub(r"\[ro.build.version.sdk\]: \[[^\]]*\]", "[ro.build.version.sdk]: [23]", text)
    return text


def shell(st: dict, cmd: str) -> tuple[int, str]:
    cmd = cmd.strip()
    if cmd == "getprop":
        return 0, getprop_text(st)
    m = re.match(r"getprop (\S+)", cmd)
    if m:
        key = m.group(1)
        if key == "sys.boot_completed":
            return 0, "1\n"
        mm = re.search(rf"\[{re.escape(key)}\]: \[([^\]]*)\]", getprop_text(st))
        return 0, (mm.group(1) if mm else "") + "\n"
    if cmd.startswith("cat /proc/meminfo"):
        total = 653000 if PROFILE == "lenovo404" else 1958344
        n_off = len(st["hidden"]) + len(st["uninstalled"]) + len(st["disabled"]) - 3
        avail = (90000 if PROFILE == "lenovo404" else 551050) + max(n_off, 0) * 15000
        return 0, f"MemTotal:       {total} kB\nMemFree:        {avail//2} kB\nMemAvailable:   {avail} kB\nCached:         {avail//3} kB\n"
    if cmd.startswith("dumpsys meminfo"):
        return 0, (FX / "meminfo.txt").read_text()
    if cmd == "id":
        return 0, "uid=2000(shell) gid=2000(shell) context=u:r:shell:s0\n"
    if cmd.startswith("pm 2>&1"):
        has_hide = PROFILE != "lenovo404"
        return 0, "usage: pm list packages\n       pm enable\n       pm disable\n" + ("       pm disable-user\n       pm hide\n       pm unhide\n" if has_hide else "")
    if cmd.startswith("pm list packages"):
        flags = cmd[len("pm list packages"):].split()
        if "-d" in flags:
            items = [p for p in st["disabled"] if p in st["packages"]]
        elif "-u" in flags:
            items = sorted(st["packages"])
        elif "-3" in flags:
            items = [p for p in visible(st) if st["packages"][p].startswith("/data/")]
        elif "-s" in flags:
            items = [p for p in visible(st) if not st["packages"][p].startswith("/data/")]
        elif "-e" in flags:
            items = [p for p in visible(st) if p not in st["disabled"]]
        else:
            items = visible(st)
        with_path = "-f" in flags
        return 0, "".join(f"package:{st['packages'][p] + '=' if with_path else ''}{p}\n" for p in items)
    if cmd.startswith("settings list"):
        return 0, "adb_enabled=1\nwindow_animation_scale=1.0\n"
    if cmd.startswith("ps"):
        return 0, "USER PID NAME\nsystem 1730 system_server\n"
    if cmd.startswith("dumpsys package"):
        m = re.match(r"dumpsys package (\S+)$", cmd)
        if m:
            p = m.group(1)
            if p not in st["packages"]:
                return 0, "Unable to find package: %s\n" % p
            en = 3 if p in st["disabled"] else 0
            return 0, f"Package [{p}] (abc):\n    User 0:  installed={'false' if p in st['uninstalled'] else 'true'} hidden={'true' if p in st['hidden'] else 'false'} stopped=false notLaunched=false enabled={en}\n"
        return 0, "Packages:\n" + "".join(f"  Package [{p}]\n" for p in st["packages"])
    if cmd.startswith("dumpsys power"):
        return 0, "  mWakefulness=Awake\n  mWakefulnessChanging=false\nDisplay Power: state=ON\n"
    if cmd.startswith("dumpsys activity activities"):
        launcher = "com.jamdeo.launcher/.app.DefaultHomeActivity" if PROFILE != "lenovo404" else "com.lenovo.ailauncher/.Main"
        return 0, f"  mFocusedActivity: ActivityRecord{{3ceefa89 u0 {launcher} t1467}}\n  mResumedActivity: ActivityRecord{{3ceefa89 u0 {launcher} t1467}}\n"
    # ---- 改状态的命令 ----
    m = re.match(r"pm disable-user(?: --user 0)? (\S+)", cmd)
    if m:
        p = m.group(1)
        if PROFILE in ("hisense2017", "coocaa_revert", "lenovo404"):
            return 0, SEC_EXC + "\n"
        if p not in st["packages"]:
            return 0, f"Error: java.lang.IllegalArgumentException: Unknown package: {p}\n"
        st["disabled"] = sorted(set(st["disabled"]) | {p}); return 0, f"Package {p} new state: disabled-user\n"
    m = re.match(r"pm disable (\S+)", cmd)
    if m:
        return 0, SEC_EXC + "\n"
    m = re.match(r"pm enable(?: --user 0)? (\S+)", cmd)
    if m:
        p = m.group(1); st["disabled"] = [x for x in st["disabled"] if x != p]; return 0, f"Package {p} new state: enabled\n"
    m = re.match(r"pm hide (\S+)", cmd)
    if m:
        if PROFILE == "lenovo404":
            return 0, "Error: unknown command 'hide'\nusage: pm list packages [-f] [-d] [-s] [-3]\n"
        p = m.group(1)
        if p not in st["packages"]:
            return 0, f"Error: java.lang.IllegalArgumentException: Unknown package: {p}\n"
        st["hidden"] = sorted(set(st["hidden"]) | {p})
        if ".ad" in p and PROFILE == "hisense2017" and not st["stopped_ads"]:
            st["stopped_ads"] = True; st["black_once_pending"] = True
        return 0, f"Package {p} new hidden state: true\n"
    m = re.match(r"pm unhide (\S+)", cmd)
    if m:
        if PROFILE == "lenovo404":
            return 0, "Error: unknown command 'unhide'\n"
        p = m.group(1); st["hidden"] = [x for x in st["hidden"] if x != p]; return 0, f"Package {p} new hidden state: false\n"
    m = re.match(r"pm uninstall(?: -k)?(?: --user 0)? (\S+)", cmd)
    if m:
        p = m.group(1)
        if p not in st["packages"]:
            return 0, "Failure [DELETE_FAILED_INTERNAL_ERROR]\n"
        if PROFILE == "lenovo404" and not st["packages"][p].startswith("/data/"):
            return 0, "Failure\n"
        if PROFILE == "hisense2017":
            return 0, "Failure [DELETE_FAILED_INTERNAL_ERROR]\n"
        st["uninstalled"] = sorted(set(st["uninstalled"]) | {p}); return 0, "Success\n"
    m = re.match(r"cmd package install-existing (\S+)", cmd)
    if m:
        p = m.group(1)
        if PROFILE == "lenovo404":
            return 0, "/system/bin/sh: cmd: not found\n"
        st["uninstalled"] = [x for x in st["uninstalled"] if x != p]; return 0, f"Package {p} installed for user: 0\n"
    m = re.match(r"pm install -r(?: --user 0)? (\S+)", cmd)
    if m:
        path = m.group(1)
        for p, pp in st["packages"].items():
            if pp == path:
                st["uninstalled"] = [x for x in st["uninstalled"] if x != p]; return 0, "Success\n"
        return 0, "Failure [INSTALL_FAILED_INVALID_URI]\n"
    if cmd.startswith("input keyevent") or cmd.startswith("am ") or cmd.startswith("settings put"):
        return 0, ""
    if cmd.startswith("cat /proc/uptime"):
        return 0, f"{st.get('uptime', 5000.0):.2f} 12000.00\n"
    if cmd.startswith("screencap") or cmd.startswith("uptime"):
        return 0, "up time: 00:04:06\n"
    return 127, f"/system/bin/sh: {cmd.split()[0]}: not found\n"


def png_bytes(black: bool) -> bytes:
    """一张 1x1 的真 PNG；黑屏给全黑，正常给中灰。"""
    import struct, zlib
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    w = h = 16
    row = b"\x00" + (b"\x00\x00\x00" if black else b"\x80\x80\x80") * w
    raw = row * h
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def main(argv: list[str]) -> int:
    st = load_state()
    args = list(argv)
    if args[:1] == ["-s"]:
        args = args[2:]
    if not args:
        print("Android Debug Bridge version 1.0.41 (mock)"); return 0
    cmd = args[0]
    if cmd == "version":
        print("Android Debug Bridge version 1.0.41 (mock)"); return 0
    if cmd == "connect":
        st["connected"] = True; st["serial"] = args[1] if len(args) > 1 else "192.168.1.34:5555"; save_state(st)
        print(f"connected to {st['serial']}" if st["authorized"] else f"failed to authenticate to {st['serial']}"); return 0
    if cmd == "disconnect":
        print("disconnected"); return 0
    if cmd == "kill-server":
        return 0
    if cmd == "devices":
        st["auth_polls"] += 1
        if not st["authorized"] and st["auth_polls"] >= 3:  # 第三次轮询时「用户点了允许」
            st["authorized"] = True
        save_state(st)
        state = "device" if st["authorized"] else "unauthorized"
        print(f"List of devices attached\n{st.get('serial', '192.168.1.34:5555')}\t{state}"); return 0
    if cmd == "reboot":
        st["reboots"] += 1
        st["uptime"] = 30.0 + st["reboots"]
        if st.get("black_once_pending"):
            st["black_now"] = True; st["black_once_pending"] = False
        elif st.get("black_now") and not st["hidden"]:
            st["black_now"] = False  # 全部还原后再重启：桌面回来
        elif st.get("black_now"):
            st["black_now"] = False  # 真重启一次就好（与 09-14 实证一致：一次性状态）
        if PROFILE == "coocaa_revert":
            st["hidden"] = []  # 系统把 hide 悄悄恢复
        save_state(st); return 0
    if cmd == "exec-out":
        if "screencap" in " ".join(args[1:]):
            sys.stdout.buffer.write(png_bytes(st.get("black_now", False))); return 0
        return 0
    if cmd == "shell":
        st["uptime"] = st.get("uptime", 5000.0) + 1.0
        code, out = shell(st, " ".join(args[1:]))
        save_state(st)
        sys.stdout.write(out); return code
    print(f"mock adb: unsupported {args}", file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
