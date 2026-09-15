"""探测与资格判断（阶段 0 / 2）。

resolve_target  用户给的是 IP 还是 IP:端口，统一成 adb 目标串
probe           连接 → 等授权 → 读 getprop / 内存 → 写 device.json
eligibility     只看底层系统与安卓版本给「能不能」，看内存与广告给「值不值」
identify        没连上电视时，用用户口述（品牌 / 系统名 / 版本）落到品牌与候选系列
"""
from __future__ import annotations

import re
import time

from .adbio import Adb
from .data import candidate_series, load_brands, match_brand

MB = 1024


def resolve_target(target: str) -> str:
    target = target.strip()
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", target):
        return target + ":5555"
    return target


def _mem_from_proc(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = re.match(r"(MemTotal|MemFree|MemAvailable|Cached):\s+(\d+)\s*kB", line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def base_os_of(props: dict) -> str:
    if props.get("ro.build.mktg.fireos") or "fire" in (props.get("ro.build.version.name", "") + props.get("ro.product.brand", "")).lower():
        return "fireos"
    if props.get("hw_sc.build.platform.version") or props.get("ro.build.version.emui", "").lower().startswith("harmony"):
        return "harmony"
    return "android"


def summarize_props(props: dict) -> dict:
    sdk = props.get("ro.build.version.sdk", "")
    return {
        "brand": props.get("ro.product.brand", ""),
        "manufacturer": props.get("ro.product.manufacturer", ""),
        "model": props.get("ro.product.model", ""),
        "device": props.get("ro.product.device", ""),
        "name": props.get("ro.product.name", ""),
        "android_release": props.get("ro.build.version.release", ""),
        "sdk": int(sdk) if sdk.isdigit() else None,
        "fingerprint": props.get("ro.build.fingerprint", ""),
        "build_date": props.get("ro.build.date", ""),
        "build_type": props.get("ro.build.type", ""),
        "secure": props.get("ro.secure", ""),
        "characteristics": props.get("ro.build.characteristics", ""),
        "fireos": props.get("ro.build.mktg.fireos", "") or props.get("ro.build.version.name", ""),
        "harmony": props.get("hw_sc.build.platform.version", ""),
        "base_os": base_os_of(props),
    }


def probe(adb: Adb, target: str, wait_auth: int = 90) -> dict:
    """连接并读身份。返回 dict 含 status（device / unauthorized / offline / absent）。

    授权框只在电视上弹；这里最多等 wait_auth 秒，期间每 3 秒看一次状态。
    """
    target = resolve_target(target)
    tcp = ":" in target  # emulator-5554 / USB 序列号不需要 connect
    if tcp:
        adb.connect(target)
    deadline = time.time() + wait_auth
    status = adb.state_of(target)
    while status != "device" and time.time() < deadline:
        time.sleep(3)
        if tcp and status in ("absent", "offline"):
            adb.connect(target)
        status = adb.state_of(target)
    info = {"target": target, "status": status}
    if status != "device":
        return info
    dev = Adb(serial=target, timeout=adb.timeout)
    props = dev.getprops()
    info.update(summarize_props(props))
    mem = _mem_from_proc(dev.shell("cat /proc/meminfo").out)
    info["mem_total_mb"] = round(mem.get("MemTotal", 0) / MB) if mem else None
    avail = mem.get("MemAvailable") or (mem.get("MemFree", 0) + mem.get("Cached", 0))
    info["mem_available_mb"] = round(avail / MB) if mem else None
    pk = dev.shell("pm list packages").out
    info["package_count"] = len([l for l in pk.splitlines() if l.startswith("package:")])
    info["has_pm_hide"] = "hide" in dev.shell("pm 2>&1 | head -60").out
    info["shell_uid"] = dev.shell("id").out.strip()[:80]
    return info


def match_device_brand(info: dict) -> list[dict]:
    hay = " ".join(str(info.get(k, "")) for k in ("brand", "manufacturer", "model", "name", "fingerprint", "device"))
    return match_brand(hay)


def eligibility(info: dict, brand: dict | None = None, series: dict | None = None) -> dict:
    """四选一：ok / low_gain / limited / not_applicable，附原因与下一步。"""
    base = info.get("base_os", "android")
    sdk = info.get("sdk")
    release = info.get("android_release", "")
    mem_total = info.get("mem_total_mb")
    reasons, verdict, next_step = [], "ok", ""

    if series and series.get("base_os") not in (None, "android", "fireos", "harmony"):
        verdict = "not_applicable"
        reasons.append(f"这个系列的底层系统是 {series.get('base_os')}，不是安卓，ADB 用不上。")
    elif brand and brand.get("status") == "不适用" and not info.get("status") == "device":
        verdict = "not_applicable"
        reasons.append(brand.get("not_applicable", {}).get("reason", "该品牌不开放 ADB。"))
    elif sdk is not None and sdk < 21:
        verdict = "limited"
        reasons.append(
            f"Android {release or sdk} 太老：adb 没有停用系统应用的权限，也没有 pm hide，"
            "系统自带应用一个都停不了，只能卸掉后装的第三方应用。"
        )
        next_step = "结论是「连得上，改不了」。可以只列出后装的第三方应用让用户决定卸不卸；不要再往下走停用流程。"
    else:
        if base == "harmony":
            reasons.append("鸿蒙早期版本保留安卓兼容层，ADB 可用；HarmonyOS 5 及以上（MateTV）没有 ADB。")
        if mem_total:
            if mem_total <= 2200:
                reasons.append(f"总内存约 {mem_total} MB，属于收益最大的一档（本机实测 2 GB 机型清完多出 437 MB）。")
            elif mem_total >= 3500:
                verdict = "low_gain"
                reasons.append(f"总内存约 {mem_total} MB，卡顿多半不是预装应用造成的；除非有开机广告，否则收益有限。")
        if series and series.get("worth_it_hint"):
            reasons.append("系列提示：" + series["worth_it_hint"])
        next_step = next_step or "可以继续：先备份，再做一次回滚预演，然后从广告类开始分批停。"

    if verdict == "not_applicable" and brand:
        alt = brand.get("not_applicable", {}).get("alternative")
        if alt:
            next_step = "替代动作：" + alt
    return {"verdict": verdict, "reasons": reasons, "next_step": next_step}


VERDICT_ZH = {
    "ok": "可以做",
    "low_gain": "可以做，但收益小",
    "limited": "连得上，改不了",
    "not_applicable": "不适用",
}


def identify_by_answers(brand_text: str, release: str | None = None, system_name: str | None = None) -> dict:
    """阶段 0：还没连电视，用用户的三个回答落到品牌与候选系列。"""
    hay = " ".join(x for x in (brand_text, system_name) if x)
    brands = match_brand(hay)
    if not brands:
        return {"brand": None, "candidates": [], "note": "品牌库里没有这个牌子；走通用路径（连点版本号七次），连上后再判断。"}
    b = brands[0]
    base_hint = None
    if system_name:
        s = system_name.lower()
        if "fire" in s:
            base_hint = "fireos"
        elif "harmony" in s or "鸿蒙" in s:
            base_hint = "harmony"
        elif "tizen" in s:
            base_hint = "tizen"
        elif "webos" in s:
            base_hint = "webos"
        elif "roku" in s:
            base_hint = "roku"
    cands = candidate_series(b, release, base_hint)
    return {
        "brand": b["brand"],
        "brand_file": b["_file"],
        "status": b["status"],
        "summary": b.get("summary", ""),
        "identify_hints": b.get("identify_hints", {}),
        "candidates": [
            {
                "name": s["name"],
                "years": s.get("years"),
                "base_os": s.get("base_os"),
                "android_version_range": s.get("android_version_range"),
                "identify_hints": s.get("identify_hints", ""),
                "worth_it_hint": s.get("worth_it_hint", ""),
            }
            for s in cands
        ],
        "not_applicable": b.get("not_applicable"),
    }


def all_brand_names() -> list[str]:
    return [b["brand"] for b in load_brands()]
