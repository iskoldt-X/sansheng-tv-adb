"""阶段 4–6：回滚预演、分批停用、还原。

三条停用命令按「可还原程度」排序，逐级降级：
  disable-user  →  pm enable 立刻回来，进程杀掉、桌面消失，数据不动
  hide          →  pm unhide 立刻回来；效果同上；个别系统重启后会被系统悄悄恢复（酷开实测）
  uninstall -k --user 0  →  只对当前用户「卸载」，系统分区里 apk 还在；还原要 install-existing（Android 9+）
                            或 pm install -r 系统 apk（老系统），所以排最后，而且要先预演一次能不能装回来。
每停一个包就往 rollback.sh 追加一行，先写行再算成功——中途断线也有据可查。
"""
from __future__ import annotations

import re

from .adbio import Adb
from .planner import is_keep
from .session import Session

METHODS = ("disable_user", "hide", "uninstall_user0")


def _pkg_state(adb: Adb, pkg: str) -> dict:
    """从 dumpsys package 读 User 0 的 installed / hidden / enabled。"""
    out = adb.shell(f"dumpsys package {pkg}", timeout=40).out
    st = {"present": bool(re.search(rf"Package \[{re.escape(pkg)}\]", out)), "installed": None, "hidden": None, "enabled": None}
    m = re.search(r"User 0:\s+(.*)", out)
    if m:
        seg = m.group(1)
        for key in ("installed", "hidden"):
            mm = re.search(rf"{key}=(true|false)", seg)
            if mm:
                st[key] = mm.group(1) == "true"
        mm = re.search(r"enabled=(\d)", seg)
        if mm:
            st["enabled"] = int(mm.group(1))  # 0 默认 1 启用 2 禁用 3 disabled-user 4 until-used
    return st


def _is_listed(adb: Adb, pkg: str) -> bool:
    out = adb.shell("pm list packages").out
    return f"package:{pkg}\n" in out + "\n"


def apply_method(adb: Adb, pkg: str, method: str, apk_path: str = "", sdk: int | None = None) -> tuple[bool, str, list[str]]:
    """执行一种停用方式。返回 (成功?, 命令原始输出, 还原命令行列表)。"""
    if method == "disable_user":
        res = adb.shell(f"pm disable-user --user 0 {pkg}")
        ok = "disabled" in res.out.lower() and "exception" not in res.text.lower() and "error" not in res.text.lower()
        if ok:
            ok = pkg in adb.shell("pm list packages -d").out
        return ok, res.text.strip(), [f"adb shell pm enable {pkg}"]
    if method == "hide":
        res = adb.shell(f"pm hide {pkg}")
        ok = "hidden state: true" in res.out.lower()
        if ok:
            ok = not _is_listed(adb, pkg)
        return ok, res.text.strip(), [f"adb shell pm unhide {pkg}"]
    if method == "uninstall_user0":
        res = adb.shell(f"pm uninstall -k --user 0 {pkg}")
        ok = "success" in res.out.lower()
        if ok:
            ok = not _is_listed(adb, pkg)
        lines = []
        if sdk is None or sdk >= 28:
            lines.append(f"adb shell cmd package install-existing {pkg}")
        if apk_path:
            lines.append(f"adb shell pm install -r --user 0 {apk_path}")
        if not lines:
            lines.append(f"# {pkg}：卸载后无已知还原命令（老系统），请勿使用 uninstall")
        return ok, res.text.strip(), lines
    raise ValueError(method)


def restore_method(adb: Adb, pkg: str, method: str, apk_path: str = "", sdk: int | None = None) -> tuple[bool, str]:
    if method == "disable_user":
        res = adb.shell(f"pm enable {pkg}")
        return (pkg not in adb.shell("pm list packages -d").out), res.text.strip()
    if method == "hide":
        res = adb.shell(f"pm unhide {pkg}")
        return _is_listed(adb, pkg), res.text.strip()
    if method == "uninstall_user0":
        res = adb.shell(f"cmd package install-existing {pkg}") if (sdk is None or sdk >= 28) else adb.shell(f"pm install -r --user 0 {apk_path}")
        if not _is_listed(adb, pkg) and apk_path:
            res = adb.shell(f"pm install -r --user 0 {apk_path}")
        return _is_listed(adb, pkg), res.text.strip()
    raise ValueError(method)


def dryrun(adb: Adb, sess: Session, pkg: str | None = None, allow_uninstall: bool = False) -> dict:
    """G3 回滚预演：拿一个可停包，逐种方法「停 → 验 → 还原 → 验」，记录这台机真正能用的命令。"""
    plan = sess.plan
    st = sess.state
    sdk = sess.device.get("sdk")
    if not pkg:
        for batch in plan.get("batches", []):
            if batch:
                pkg = batch[0]
                break
    if not pkg:
        return {"error": "没有可用于预演的包（可停清单为空）"}
    if is_keep(pkg, plan):
        return {"error": f"{pkg} 在禁停名单里，不能用来预演"}
    apk = next((e["path"] for e in plan["groups"]["junk"] if e["pkg"] == pkg), "")
    caps: dict[str, dict] = {}
    for method in METHODS:
        if method == "uninstall_user0" and not allow_uninstall:
            caps[method] = {"ok": None, "note": "未预演（需要 --allow-uninstall 明确允许）"}
            continue
        if method == "hide" and sess.device.get("has_pm_hide") is False:
            caps[method] = {"ok": False, "note": "这台机的 pm 没有 hide 子命令"}
            continue
        ok, out, lines = apply_method(adb, pkg, method, apk, sdk)
        if not ok:
            caps[method] = {"ok": False, "note": out[:200]}
            continue
        # 先写还原行，再尝试还原
        sess.add_rollback(lines, comment=f"预演 {method} {pkg}")
        restored, rout = restore_method(adb, pkg, method, apk, sdk)
        caps[method] = {"ok": restored, "note": ("停用成功，还原成功" if restored else f"停用成功但还原失败：{rout[:160]}")}
        if not restored:
            sess.log(f"⚠️ 预演 {method} 后 {pkg} 未能还原，已停止预演；还原命令在 rollback.sh")
            break
    usable = [m for m in METHODS if caps.get(m, {}).get("ok")]
    st["caps"] = caps
    st["method_order"] = usable
    st["dryrun_pkg"] = pkg
    st["phase"] = "rehearsed"
    sess.state = st
    sess.log(f"回滚预演（{pkg}）：可用方法 = {usable or '无'}")
    return {"pkg": pkg, "caps": caps, "usable": usable}


def apply_batch(adb: Adb, sess: Session, pkgs: list[str], label: str = "") -> dict:
    """停一批。任何一个包停完验证不过就还原它并停止本批（熔断留给上层数）。"""
    plan, st = sess.plan, sess.state
    sdk = sess.device.get("sdk")
    order = st.get("method_order") or []
    if not order:
        return {"error": "还没做回滚预演（dryrun），不知道这台机哪条命令能用"}
    done, skipped, failed = [], [], []
    paths = {e["pkg"]: e["path"] for g in plan["groups"].values() for e in g}
    for pkg in pkgs:
        why = is_keep(pkg, plan)
        if why:
            skipped.append({"pkg": pkg, "reason": "禁停：" + why})
            continue
        applied = None
        for method in order:
            ok, out, lines = apply_method(adb, pkg, method, paths.get(pkg, ""), sdk)
            if ok:
                sess.add_rollback(lines, comment=f"{label} {pkg} via {method}")
                applied = {"pkg": pkg, "method": method, "restore": lines}
                break
        if applied:
            done.append(applied)
            sess.note_action(kind="disable", **applied)
        else:
            failed.append({"pkg": pkg, "reason": "所有可用方法都失败"})
    st = sess.state
    st["batches"].append({"label": label, "done": done, "skipped": skipped, "failed": failed})
    st["phase"] = "batch_applied"
    sess.state = st
    sess.log(f"{label}：停用 {len(done)}，跳过 {len(skipped)}，失败 {len(failed)}")
    return {"done": done, "skipped": skipped, "failed": failed}


def active_done(st: dict) -> list[dict]:
    """仍处于停用状态（还没被我们还原）的记录。"""
    return [a for b in st.get("batches", []) for a in b.get("done", []) if not a.get("restored_at")]


def restore(adb: Adb, sess: Session, scope: str = "last", pkg: str | None = None) -> dict:
    """还原：last = 最近一批（仍在停用状态的）；all = 全部；pkg = 单个。以 state.json 的记录为准，不猜。"""
    st = sess.state
    sdk = sess.device.get("sdk")
    batches = st.get("batches", [])
    targets: list[dict] = []
    if pkg:
        targets = [a for b in batches for a in b["done"] if a["pkg"] == pkg and not a.get("restored_at")]
    elif scope == "last":
        for b in reversed(batches):
            live = [a for a in b["done"] if not a.get("restored_at")]
            if live:
                targets = live
                break
    elif scope == "all":
        targets = active_done(st)
    results = []
    plan = sess.plan
    paths = {e["pkg"]: e["path"] for g in plan.get("groups", {}).values() for e in g}
    from .session import now_iso
    for a in reversed(targets):
        ok, out = restore_method(adb, a["pkg"], a["method"], paths.get(a["pkg"], ""), sdk)
        results.append({"pkg": a["pkg"], "method": a["method"], "restored": ok, "out": out[:120]})
        if ok:
            a["restored_at"] = now_iso()
    st["batches"] = batches
    sess.state = st
    for r in results:
        sess.note_action(kind="restore", pkg=r["pkg"], method=r["method"], ok=r["restored"])
    sess.log(f"还原（{pkg or scope}）：{sum(r['restored'] for r in results)}/{len(results)} 成功")
    return {"results": results}


def currently_off(adb: Adb) -> set[str]:
    """现在处于停用/隐藏/用户卸载状态的包（用于重启后复查）。"""
    listed = set(l[8:] for l in adb.shell("pm list packages").out.splitlines() if l.startswith("package:"))
    with_u = set(l[8:] for l in adb.shell("pm list packages -u").out.splitlines() if l.startswith("package:"))
    disabled = set(l[8:] for l in adb.shell("pm list packages -d").out.splitlines() if l.startswith("package:"))
    return (with_u - listed) | disabled
