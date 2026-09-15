"""阶段 3–5：备份、分组。

backup   四类清单 + getprop + 内存 + 全量 dumpsys package，写 sha256 清单
plan     把电视上的包分三组：junk（可自动停）/ ask（必须问用户）/ keep（脚本层拒绝）/ unknown（默认不碰）
         判定顺序：品牌名单 → 通用禁停模式 → 通用垃圾提示模式；禁停永远压过垃圾。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .adbio import Adb
from .data import compile_patterns, load_generic_rules, pkg_glob_to_regex
from .session import Session

BACKUP_CMDS = {
    "packages_all.txt": "pm list packages -f",
    "packages_enabled.txt": "pm list packages -e",
    "packages_disabled.txt": "pm list packages -d",
    "packages_system.txt": "pm list packages -s",
    "packages_third.txt": "pm list packages -3",
    "packages_uninstalled_included.txt": "pm list packages -u",
    "meminfo.txt": "dumpsys meminfo",
    "proc_meminfo.txt": "cat /proc/meminfo",
    "getprop.txt": "getprop",
    "settings_global.txt": "settings list global",
    "settings_system.txt": "settings list system",
    "settings_secure.txt": "settings list secure",
    "running_processes.txt": "ps -A 2>/dev/null || ps",
    "dumpsys_package.txt": "dumpsys package",
}


def backup(adb: Adb, sess: Session) -> dict:
    bdir = sess.dir / "backup"
    manifest = {}
    for name, cmd in BACKUP_CMDS.items():
        res = adb.shell(cmd, timeout=120)
        path = bdir / name
        path.write_text(res.out, encoding="utf-8")
        manifest[name] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "exit": res.code,
        }
    (bdir / "MANIFEST.sha256").write_text(
        "\n".join(f"{v['sha256']}  {k}" for k, v in manifest.items()) + "\n", encoding="utf-8"
    )
    st = sess.state
    st["backup"] = {"dir": str(bdir), "files": len(manifest)}
    st["phase"] = "backed_up"
    sess.state = st
    sess.log(f"备份完成：{len(manifest)} 个文件写入 {bdir}")
    return manifest


def parse_package_list(text: str) -> dict[str, str]:
    """`pm list packages -f` → {pkg: apk_path}；无 -f 时 path 为空串。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        body = line[len("package:"):]
        if "=" in body and body.startswith("/"):
            path, _, pkg = body.rpartition("=")
            out[pkg] = path
        else:
            out[body] = ""
    return out


def classify(packages: dict[str, str], series: dict | None, disabled: set[str] | None = None) -> dict:
    rules = load_generic_rules()
    keep_pat = compile_patterns(rules["must_keep_patterns"])
    junk_pat = compile_patterns(rules["junk_hint_patterns"])
    protected = compile_patterns(rules.get("protected_known", []))
    ask_labels = set(rules.get("ask_user_labels", []))

    brand_keep = [(pkg_glob_to_regex(p["pkg"]), p) for p in (series or {}).get("must_keep", [])]
    brand_junk = [(pkg_glob_to_regex(p["pkg"]), p) for p in (series or {}).get("junk", [])]
    brand_ask = [(pkg_glob_to_regex(p["pkg"]), p) for p in (series or {}).get("ask_user", [])]

    groups: dict[str, list[dict]] = {"keep": [], "junk": [], "ask": [], "unknown": []}
    disabled = disabled or set()
    for pkg in sorted(packages):
        entry = {"pkg": pkg, "path": packages[pkg], "already_disabled": pkg in disabled}
        placed = False
        for rx, meta in brand_keep:
            if rx.search(pkg):
                entry.update(reason=f"品牌禁停：{meta.get('what','')} {meta.get('why','')}".strip(), source="brand")
                groups["keep"].append(entry); placed = True; break
        if placed:
            continue
        for rx, meta in protected + keep_pat:
            if rx.search(pkg):
                entry.update(reason=f"通用禁停：{meta.get('why','')}", source="generic")
                groups["keep"].append(entry); placed = True; break
        if placed:
            continue
        for rx, meta in brand_ask:
            if rx.search(pkg):
                entry.update(reason=f"品牌数据：{meta.get('what','')}（看你用不用）", source="brand")
                groups["ask"].append(entry); placed = True; break
        if placed:
            continue
        for rx, meta in brand_junk:
            if rx.search(pkg):
                entry.update(reason=f"品牌数据：{meta.get('what','')}", source="brand", evidence=meta.get("evidence_level"))
                groups["junk"].append(entry); placed = True; break
        if placed:
            continue
        for rx, meta in junk_pat:
            if rx.search(pkg):
                label = meta["label"]
                entry.update(reason=f"按包名猜测：{label}", source="guess")
                (groups["ask"] if label in ask_labels else groups["junk"]).append(entry)
                placed = True
                break
        if not placed:
            groups["unknown"].append(entry)
    return groups


def make_plan(sess: Session, series: dict | None, mode: str = "safe", batch_size: int = 10, confirm_guessed: bool = False, include: list[str] | None = None) -> dict:
    """写 plan.json。safe 模式只排 junk；advanced 模式把 ask 组也列出来等用户勾选（默认全部不勾）。

    confirm_guessed：用户已经确认「按包名猜的垃圾」可以停，把它们也排进批次。
    include：用户在 ask 组里勾选的包（进阶模式），追加到批次末尾。"""
    bdir = sess.dir / "backup"
    packages = parse_package_list((bdir / "packages_all.txt").read_text(encoding="utf-8"))
    disabled = set(parse_package_list((bdir / "packages_disabled.txt").read_text(encoding="utf-8")))
    groups = classify(packages, series, disabled)
    # 猜测出来的垃圾单独标记：只有品牌数据里有来源的才默认排进批次，猜测的要用户确认
    confirmed = [e for e in groups["junk"] if e.get("source") == "brand" and not e["already_disabled"]]
    guessed = [e for e in groups["junk"] if e.get("source") == "guess" and not e["already_disabled"]]
    queue = list(confirmed) + (list(guessed) if confirm_guessed else [])
    if include:
        wanted = set(include)
        queue += [e for e in groups["ask"] if e["pkg"] in wanted and not e["already_disabled"]]
    batches = [queue[i:i + batch_size] for i in range(0, len(queue), batch_size)]
    plan = {
        "mode": mode,
        "series": (series or {}).get("name"),
        "counts": {k: len(v) for k, v in groups.items()},
        "groups": groups,
        "batches": [[e["pkg"] for e in b] for b in batches],
        "confirm_guessed": confirm_guessed,
        "included_ask": list(include or []),
        "needs_confirmation": {
            "guessed_junk": [] if confirm_guessed else [e["pkg"] for e in guessed],
            "ask_user": [e["pkg"] for e in groups["ask"] if not e["already_disabled"]],
        },
    }
    sess.plan = plan
    sess.log(
        f"分组：禁停 {plan['counts']['keep']} / 可停 {len(confirmed)}（猜测 {len(guessed)}{'，已确认' if confirm_guessed else ' 待确认'}）/ "
        f"需问用户 {plan['counts']['ask']} / 未知 {plan['counts']['unknown']}；排成 {len(batches)} 批"
    )
    return plan


def render_plan_table(plan: dict, show_unknown: bool = False) -> str:
    lines = []
    g = plan["groups"]
    lines.append(f"禁停（脚本层拒绝，共 {len(g['keep'])} 个）：只列前 12 个")
    for e in g["keep"][:12]:
        lines.append(f"  - {e['pkg']}  ← {e['reason']}")
    lines.append(f"\n可停（有来源，默认排进批次，共 {plan['counts']['junk'] - len(plan['needs_confirmation']['guessed_junk'])} 个）：")
    for e in g["junk"]:
        if e.get("source") == "brand":
            lines.append(f"  - {e['pkg']}  ← {e['reason']}")
    if plan["needs_confirmation"]["guessed_junk"]:
        lines.append("\n按包名猜测是垃圾、需要你确认后才停：")
        for e in g["junk"]:
            if e.get("source") == "guess":
                lines.append(f"  - {e['pkg']}  ← {e['reason']}")
    if plan["needs_confirmation"]["ask_user"]:
        lines.append("\n看你用不用（语音 / 投屏 / 商店 / 账号 / 第三方影音），用就留：")
        for e in g["ask"]:
            lines.append(f"  - {e['pkg']}  ← {e['reason']}")
    if show_unknown:
        lines.append(f"\n未知（默认不碰，共 {len(g['unknown'])} 个）：")
        for e in g["unknown"]:
            lines.append(f"  - {e['pkg']}")
    lines.append(f"\n批次：{len(plan['batches'])} 批，每批 ≤ {max((len(b) for b in plan['batches']), default=0)} 个")
    return "\n".join(lines)


def is_keep(pkg: str, plan: dict) -> str | None:
    for e in plan.get("groups", {}).get("keep", []):
        if e["pkg"] == pkg:
            return e["reason"]
    return None
