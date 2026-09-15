"""阶段 7–8：真重启验证与异常固定流程。

为什么一定是 adb reboot：遥控器上的关机键是待机，系统根本没重新起来；09-14 那次黑屏就是在
「待机式重启」下被掩盖了好几批，直到真重启才暴露。

verify_reboot  reboot → 等回连 → boot_completed → 三项检查（画面不黑 / 桌面在前台 / 内存）
               + 复查上一批停掉的包是否还在停用状态（酷开的 hide 会被系统在重启后恢复）
status         不重启，只看电视现在的状态（黑屏时第一步先跑它，证明「电视没坏，系统在运行」）
"""
from __future__ import annotations

import io
import re
import time

from .adbio import Adb
from .ops import active_done, currently_off
from .session import Session


def screen_brightness(png: bytes) -> float | None:
    """0-255 平均亮度；没有 Pillow 就返回 None，由文件大小兜底判断。"""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(png)).convert("L")
        im.thumbnail((192, 108))
        data = list(im.getdata())
        return sum(data) / len(data)
    except Exception:
        return None


def status(adb: Adb) -> dict:
    info: dict = {}
    power = adb.shell("dumpsys power", timeout=30).out
    m = re.search(r"mWakefulness=(\w+)", power)
    info["wakefulness"] = m.group(1) if m else "unknown"
    m = re.search(r"Display Power: state=(\w+)", power)
    info["display_power"] = m.group(1) if m else "unknown"
    act = adb.shell("dumpsys activity activities", timeout=30).out
    m = re.search(r"(mResumedActivity|mFocusedActivity|topResumedActivity)[^\n]*?\{[^}]*?\s(\S+/\S+)", act)
    info["focused"] = m.group(2) if m else ""
    info["launcher_focused"] = bool(re.search(r"launcher|tvhome|home", info["focused"], re.I))
    png = adb.exec_out("screencap -p", timeout=60)
    info["screenshot_bytes"] = len(png)
    b = screen_brightness(png)
    info["brightness"] = round(b, 1) if b is not None else None
    if b is not None:
        info["screen_black"] = b < 2.0
    else:
        info["screen_black"] = 0 < len(png) < 60_000
    mem = adb.shell("cat /proc/meminfo").out
    m = re.search(r"MemAvailable:\s+(\d+)", mem) or re.search(r"MemFree:\s+(\d+)", mem)
    info["mem_available_mb"] = round(int(m.group(1)) / 1024) if m else None
    info["boot_completed"] = adb.boot_completed()
    return info


SETTLE_SECONDS = 20  # 包管理器把停用状态写盘有约 10 秒延迟；模拟器实测：改完立刻 reboot 状态会丢，等 20 秒就保住


def _uptime(adb: Adb) -> float | None:
    out = adb.shell("cat /proc/uptime").out.split()
    try:
        return float(out[0])
    except (IndexError, ValueError):
        return None


def _last_change_age(sess: Session) -> float | None:
    acts = sess.state.get("actions") or []
    if not acts:
        return None
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat(acts[-1]["at"])
        return (_dt.datetime.now(t.tzinfo) - t).total_seconds()
    except Exception:
        return None


def verify_reboot(adb: Adb, sess: Session, target: str, timeout: int = 240) -> dict:
    st = sess.state
    expected_off = {a["pkg"] for a in active_done(st)}
    age = _last_change_age(sess)
    if age is not None and age < SETTLE_SECONDS:
        wait = SETTLE_SECONDS - age
        sess.log(f"最近一次改动距今 {age:.0f} 秒，先等 {wait:.0f} 秒让系统把停用状态写盘再重启")
        time.sleep(wait)
    uptime_before = _uptime(adb)
    sess.log("真重启验证开始（adb reboot）")
    adb.reboot()
    time.sleep(8)
    state = adb.wait_for(target, "device", timeout=timeout)
    result: dict = {"reconnected": state == "device", "state": state, "uptime_before": uptime_before}
    if state != "device":
        result["advice"] = (
            "电视重启后没有回连。先看电视画面：如果开机画面正常只是网络还没起来，再等一分钟重跑 verify；"
            "如果电视上重新弹了授权框，勾「一律允许」后重跑。"
        )
        sess.log(f"重启后未回连：{state}")
        return result
    dev = Adb(serial=target, timeout=adb.timeout)
    deadline = time.time() + 120
    # 真重启的判据：boot_completed 回到 1 且 uptime 比重启前小；否则可能只是 adb 掉线又回来
    while time.time() < deadline:
        up = _uptime(dev)
        if dev.boot_completed() and up is not None and (uptime_before is None or up < uptime_before):
            break
        time.sleep(4)
    up = _uptime(dev)
    result["uptime_after"] = up
    result["rebooted"] = bool(up is not None and (uptime_before is None or up < uptime_before))
    # 包管理器就绪：pm list 有输出
    for _ in range(15):
        if dev.shell("pm list packages").out.strip():
            break
        time.sleep(3)
    time.sleep(8)  # 让桌面把内容画出来
    result.update(status(dev))
    off_now = currently_off(dev)
    came_back = sorted(expected_off - off_now)
    result["still_off"] = len(expected_off & off_now)
    result["came_back"] = came_back
    checks = {
        "rebooted": result.get("rebooted", False),
        "screen": not result.get("screen_black"),
        "launcher": result.get("launcher_focused", False) or not result.get("screen_black"),
        "boot": result.get("boot_completed", False),
        "persist": not came_back,
    }
    result["checks"] = checks
    result["ok"] = all(checks.values())
    st = sess.state
    st.setdefault("verifications", []).append({k: v for k, v in result.items() if k != "advice"})
    st["phase"] = "verified" if result["ok"] else "verify_failed"
    sess.state = st
    sess.log(
        f"真重启验证：{'通过' if result['ok'] else '未通过'}；{'真重启' if result.get('rebooted') else '⚠ 未观察到重启（uptime 没变小）'}；画面{'黑' if result.get('screen_black') else '正常'}，"
        f"前台 {result.get('focused') or '未知'}，可用内存 {result.get('mem_available_mb')} MB，"
        f"停用状态保持 {result['still_off']}/{len(expected_off)}"
        + (f"，被系统恢复：{came_back}" if came_back else "")
    )
    return result


def recover_black_screen(adb: Adb, sess: Session, target: str) -> dict:
    """G8 固定顺序：查状态 → 整批还原 → 真重启 → 再查。二分留给上层按批做。"""
    from .ops import restore

    steps = []
    s0 = status(adb)
    steps.append({"step": "status", "result": s0})
    sess.log(f"黑屏处理：电源 {s0.get('display_power')} / 唤醒 {s0.get('wakefulness')} / 前台 {s0.get('focused')} / 画面{'黑' if s0.get('screen_black') else '正常'}")
    r = restore(adb, sess, scope="last")
    steps.append({"step": "restore_last", "result": r})
    v = verify_reboot(adb, sess, target)
    steps.append({"step": "reboot", "result": {k: v.get(k) for k in ("reconnected", "screen_black", "focused", "ok")}})
    if not v.get("reconnected") or v.get("screen_black"):
        r2 = restore(adb, sess, scope="all")
        steps.append({"step": "restore_all", "result": r2})
        v2 = verify_reboot(adb, sess, target)
        steps.append({"step": "reboot_again", "result": {k: v2.get(k) for k in ("reconnected", "screen_black", "focused", "ok")}})
    return {"steps": steps}
