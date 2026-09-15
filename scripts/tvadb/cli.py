"""命令行入口：python3 scripts/tvadb.py <子命令> …

每个子命令对应阶段机的一步；agent 按 SKILL.md 的顺序调用，用户在中间回话。
所有子命令都支持 --json（给 agent 读）和默认的人读输出。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import devices, ops, planner, report, verify
from .adbio import Adb, AdbNotFound
from .data import candidate_series, load_brands, match_brand
from .session import Session


def _print(obj, as_json: bool, human: str | None = None):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        print(human if human is not None else json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _adb_for(sess: Session) -> tuple[Adb, str]:
    target = sess.state.get("target") or sess.device.get("target")
    if not target:
        raise SystemExit("这个工作目录还没有电视地址；先跑 probe。")
    return Adb(serial=target), target


# ---- 阶段 0：认机型 -----------------------------------------------------------
def cmd_identify(a):
    res = devices.identify_by_answers(a.brand, a.version, a.system)
    if a.json:
        return _print(res, True)
    if not res.get("brand"):
        print(res["note"]); return
    print(f"品牌：{res['brand']}（{res['status']}）")
    if res.get("summary"):
        print(f"一句话：{res['summary']}")
    if res.get("not_applicable"):
        na = res["not_applicable"]
        print(f"不适用原因：{na.get('reason','')}\n替代动作：{na.get('alternative','')}")
    hints = res.get("identify_hints", {})
    if hints:
        print("怎么认自己的机型：")
        for k, v in hints.items():
            if v:
                print(f"  - {k}: {v if isinstance(v, str) else '；'.join(v)}")
    print(f"候选系列 {len(res['candidates'])} 个：")
    for i, s in enumerate(res["candidates"]):
        print(f"  [{i}] {s['name']}｜{s.get('years','')}｜{s.get('base_os','')} {s.get('android_version_range','') or ''}")
        if s.get("identify_hints"):
            print(f"      认：{s['identify_hints']}")
        if s.get("worth_it_hint"):
            print(f"      值：{s['worth_it_hint']}")


def cmd_entry(a):
    brands = match_brand(a.brand)
    if not brands:
        raise SystemExit("品牌库里没有这个牌子；走通用路径：设置 → 系统/关于 → 连按版本号七次 → 开发者选项 → USB 调试。")
    b = brands[0]
    series = b["series"]
    if a.series is not None:
        series = [series[a.series]] if isinstance(a.series, int) and a.series < len(series) else [s for s in series if a.series in s["name"]]
    out = {"brand": b["brand"], "series": []}
    for s in series:
        item = {"name": s["name"], "years": s.get("years"), "base_os": s.get("base_os"), "entries": s.get("entries", []), "pm_caps": s.get("pm_caps", {}), "pitfalls": s.get("pitfalls", [])}
        out["series"].append(item)
    if a.json:
        return _print(out, True)
    print(f"品牌：{b['brand']}")
    for s in out["series"]:
        print(f"\n== {s['name']}（{s.get('years','')}，{s.get('base_os','')}）")
        for e in s["entries"]:
            print(f"  · {e.get('applies_to','')}  [{e.get('evidence_level','')}]")
            for p in e.get("prerequisites", []) or []:
                print(f"      前提：{p}")
            for i, step in enumerate(e.get("steps", []), 1):
                print(f"      {i}. {step}")
            if e.get("risk_notes"):
                print(f"      ⚠ {e['risk_notes']}")
            if e.get("has_auth_prompt") is not None:
                print(f"      授权弹窗：{'有' if e['has_auth_prompt'] else '无'}；配对码：{'要' if e.get('wireless_pairing') else '不要'}；端口 {e.get('port') or '?'}")
            for src in e.get("sources", [])[:3]:
                print(f"      来源：{src.get('title','')} {src.get('url','')}")
        caps = s.get("pm_caps") or {}
        if caps:
            print(f"  命令能力（数据）：disable-user={caps.get('disable_user')} hide={caps.get('hide')} uninstall-user0={caps.get('uninstall_user0')}")
        for p in s.get("pitfalls", [])[:5]:
            print(f"  坑：{p.get('what','')}")


def cmd_brands(a):
    for b in load_brands():
        print(f"{b['status']:<5} {b['brand']}  ({len(b.get('series',[]))} 个系列)")


# ---- 阶段 2：连接 + 资格 ----------------------------------------------------
def cmd_probe(a):
    try:
        adb = Adb()
    except AdbNotFound as e:
        raise SystemExit(str(e))
    info = devices.probe(adb, a.target, wait_auth=a.wait)
    if info["status"] != "device":
        msg = {
            "unauthorized": "电视上应该弹了「允许 USB 调试」的框：勾「一律允许」再点允许，然后重跑 probe。",
            "offline": "连上了但离线：先 adb kill-server，把电视退回主界面（别停在开发者页），再重跑。",
            "absent": "连不上：确认电视和电脑在同一个 Wi-Fi、IP 没变、USB/网络调试已打开；Android 11+ 可能要先 adb pair 配对码。",
        }.get(info["status"], "")
        return _print(info, a.json, f"状态：{info['status']}。{msg}")
    sess = Session(a.workdir) if a.workdir else Session.create(info.get("model") or info.get("brand") or "tv")
    brands = devices.match_device_brand(info)
    b = brands[0] if brands else None
    cands = candidate_series(b, info.get("android_release"), info.get("base_os")) if b else []
    series = cands[0] if len(cands) == 1 else None
    elig = devices.eligibility(info, b, series)
    info.update({"brand_match": b["brand"] if b else None, "brand_file": b["_file"] if b else None,
                 "series_candidates": [s["name"] for s in cands], "series": series["name"] if series else None,
                 "eligibility": elig})
    sess.device = info
    st = sess.state
    st["target"] = info["target"]; st["phase"] = "probed"
    sess.state = st
    sess.log(f"连接 {info['target']}：{info.get('brand')} {info.get('model')} Android {info.get('android_release')}（sdk {info.get('sdk')}），"
             f"内存 {info.get('mem_total_mb')} MB，{info.get('package_count')} 个包；资格：{devices.VERDICT_ZH[elig['verdict']]}")
    human = [
        f"已连接 {info['target']}",
        f"品牌/型号：{info.get('brand')} / {info.get('model')}（{info.get('manufacturer')}）",
        f"系统：{info.get('base_os')} Android {info.get('android_release')}（API {info.get('sdk')}），固件 {info.get('build_date')}",
        f"内存：总 {info.get('mem_total_mb')} MB，可用 {info.get('mem_available_mb')} MB；应用 {info.get('package_count')} 个；pm hide {'有' if info.get('has_pm_hide') else '无'}",
        f"品牌库匹配：{info['brand_match'] or '未命中（走通用规则）'}；候选系列：{info['series_candidates'] or '无'}",
        f"资格：{devices.VERDICT_ZH[elig['verdict']]}",
    ] + [f"  - {r}" for r in elig["reasons"]] + [f"下一步：{elig['next_step']}", f"工作目录：{sess.dir}"]
    _print(info, a.json, "\n".join(human))


def cmd_series(a):
    sess = Session.open(a.workdir)
    dev = sess.device
    brands = [b for b in load_brands() if b["_file"] == dev.get("brand_file")] or devices.match_device_brand(dev)
    if not brands:
        raise SystemExit("device.json 没有品牌匹配，无法设定系列")
    b = brands[0]
    hits = [s for s in b["series"] if a.name in s["name"]]
    if len(hits) != 1:
        raise SystemExit(f"系列名不唯一或没找到：{[s['name'] for s in b['series']]}")
    dev["series"] = hits[0]["name"]
    dev["eligibility"] = devices.eligibility(dev, b, hits[0])
    sess.device = dev
    _print(dev["eligibility"], a.json, f"系列设为：{hits[0]['name']}；资格：{devices.VERDICT_ZH[dev['eligibility']['verdict']]}")


def _series_of(sess: Session) -> dict | None:
    dev = sess.device
    for b in load_brands():
        if b["_file"] == dev.get("brand_file"):
            for s in b["series"]:
                if s["name"] == dev.get("series"):
                    return s
    return None


# ---- 阶段 3–6 -------------------------------------------------------------
def cmd_backup(a):
    sess = Session.open(a.workdir)
    adb, _ = _adb_for(sess)
    m = planner.backup(adb, sess)
    _print(m, a.json, f"备份 {len(m)} 个文件 → {sess.dir / 'backup'}（sha256 清单 MANIFEST.sha256）")


def cmd_plan(a):
    sess = Session.open(a.workdir)
    if not (sess.dir / "backup" / "packages_all.txt").exists():
        raise SystemExit("还没备份；先跑 backup（没有备份不允许分组停用）")
    include = [x.strip() for x in (a.include or "").split(",") if x.strip()]
    plan = planner.make_plan(sess, _series_of(sess), a.mode, a.batch_size, a.confirm_guessed, include)
    _print(plan, a.json, planner.render_plan_table(plan, a.show_unknown))


def cmd_dryrun(a):
    sess = Session.open(a.workdir)
    adb, _ = _adb_for(sess)
    res = ops.dryrun(adb, sess, a.pkg, a.allow_uninstall)
    if "error" in res:
        raise SystemExit(res["error"])
    human = [f"预演包：{res['pkg']}"] + [f"  {m}: {'可用' if v.get('ok') else ('未预演' if v.get('ok') is None else '不可用')} —— {v.get('note','')}" for m, v in res["caps"].items()]
    human.append(f"这台机可用的停用方式：{res['usable'] or '无（不能继续停用）'}")
    _print(res, a.json, "\n".join(human))


def cmd_batch(a):
    sess = Session.open(a.workdir)
    adb, _ = _adb_for(sess)
    plan = sess.plan
    if a.pkgs:
        pkgs = [p.strip() for p in a.pkgs.split(",") if p.strip()]
        label = a.label or "自定义批"
    else:
        idx = a.n - 1
        if idx < 0 or idx >= len(plan.get("batches", [])):
            raise SystemExit(f"没有第 {a.n} 批（共 {len(plan.get('batches', []))} 批）")
        pkgs, label = plan["batches"][idx], a.label or f"第 {a.n} 批"
    if len(pkgs) > 10:
        raise SystemExit("一批最多 10 个（小批量是安全闸，不能绕）")
    res = ops.apply_batch(adb, sess, pkgs, label)
    if "error" in res:
        raise SystemExit(res["error"])
    human = [f"{label}：停用 {len(res['done'])} 个"] + [f"  ✓ {d['pkg']}（{d['method']}）" for d in res["done"]]
    human += [f"  ↷ 跳过 {s['pkg']}：{s['reason']}" for s in res["skipped"]] + [f"  ✗ 失败 {f['pkg']}：{f['reason']}" for f in res["failed"]]
    human.append(f"还原脚本：{sess.rollback}")
    _print(res, a.json, "\n".join(human))


def cmd_verify(a):
    sess = Session.open(a.workdir)
    adb, target = _adb_for(sess)
    res = verify.verify_reboot(adb, sess, target, a.timeout)
    if not res.get("reconnected"):
        return _print(res, a.json, f"重启后未回连（{res['state']}）。{res.get('advice','')}")
    c = res["checks"]
    human = [
        f"真重启验证：{'通过' if res['ok'] else '未通过'}" + ("" if res.get("rebooted") else "（⚠ 没观察到真正重启：uptime 没变小，可能是待机或 adb 掉线又回来）"),
        f"  画面：{'黑屏' if res.get('screen_black') else '正常'}（亮度 {res.get('brightness')}）",
        f"  前台：{res.get('focused') or '未知'}{'（桌面）' if res.get('launcher_focused') else ''}",
        f"  可用内存：{res.get('mem_available_mb')} MB",
        f"  停用状态保持：{res['still_off']} 个" + (f"；被系统恢复了 {len(res['came_back'])} 个：{res['came_back']}（这台机对该方法无效，换下一级方法）" if res["came_back"] else ""),
    ]
    if not c["screen"]:
        human.append("  → 黑屏：电视没坏，系统在运行。跑 recover（查状态 → 还原最近一批 → 真重启），别拔电源。")
    _print(res, a.json, "\n".join(human))


def cmd_status(a):
    sess = Session.open(a.workdir)
    adb, _ = _adb_for(sess)
    s = verify.status(adb)
    off = ops.currently_off(adb)
    baseline = {e["pkg"] for g in sess.plan.get("groups", {}).values() for e in g if e.get("already_disabled")}
    keep_off = [e["pkg"] for e in sess.plan.get("groups", {}).get("keep", []) if e["pkg"] in off and e["pkg"] not in baseline]
    s["keep_currently_off"] = keep_off
    human = [
        f"电源 {s['display_power']} / 唤醒 {s['wakefulness']} / 启动完成 {s['boot_completed']}",
        f"前台：{s['focused'] or '未知'}；画面：{'黑' if s['screen_black'] else '正常'}（亮度 {s['brightness']}）",
        f"可用内存：{s['mem_available_mb']} MB；当前停用/隐藏 {len(off)} 个",
    ]
    if keep_off:
        human.append(f"⚠ 禁停名单里有 {len(keep_off)} 个处于停用状态，必须恢复：{keep_off}")
    _print(s, a.json, "\n".join(human))


def cmd_recover(a):
    sess = Session.open(a.workdir)
    adb, target = _adb_for(sess)
    res = verify.recover_black_screen(adb, sess, target)
    _print(res, a.json, "\n".join(f"{s['step']}: {json.dumps(s['result'], ensure_ascii=False, default=str)[:300]}" for s in res["steps"]))


def cmd_restore(a):
    sess = Session.open(a.workdir)
    adb, _ = _adb_for(sess)
    scope = "all" if a.all else "last"
    res = ops.restore(adb, sess, scope, a.pkg)
    _print(res, a.json, "\n".join(f"{'✓' if r['restored'] else '✗'} {r['pkg']}（{r['method']}）{'' if r['restored'] else r['out']}" for r in res["results"]) or "没有可还原的记录")


def cmd_report(a):
    sess = Session.open(a.workdir)
    text = report.build_report(sess, a.mem_after)
    card = report.build_card(sess, a.mem_after) if a.card else None
    print(text)
    if card:
        print(f"\n结果卡：{card}")


def cmd_resume(a):
    sess = Session.open(a.workdir)
    st, dev = sess.state, sess.device
    done = ops.active_done(st)
    human = [
        f"工作目录：{sess.dir}",
        f"电视：{dev.get('brand')} {dev.get('model')} Android {dev.get('android_release')}；目标 {st.get('target')}",
        f"阶段：{st.get('phase')}；可用方法：{st.get('method_order') or '未预演'}",
        f"已停用 {len(done)} 个（{len(st.get('batches', []))} 批）；还原脚本 {sess.rollback}",
    ]
    _print({"dir": str(sess.dir), "state": st, "device": dev}, a.json, "\n".join(human))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tvadb", description="电视 ADB 清理（安全阶段机）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, workdir=True):
        sp.add_argument("--json", action="store_true")
        if workdir:
            sp.add_argument("--workdir", help="清理工作目录（默认最近一次）")

    s = sub.add_parser("identify", help="阶段 0：按品牌/系统名/版本落到候选系列"); common(s, False)
    s.add_argument("--brand", required=True); s.add_argument("--version"); s.add_argument("--system"); s.set_defaults(fn=cmd_identify)
    s = sub.add_parser("entry", help="阶段 1：打印某品牌/系列的开发者模式入口步骤"); common(s, False)
    s.add_argument("--brand", required=True); s.add_argument("--series", help="系列序号或名称片段"); s.set_defaults(fn=cmd_entry)
    s = sub.add_parser("brands", help="列出品牌库"); common(s, False); s.set_defaults(fn=cmd_brands)
    s = sub.add_parser("probe", help="阶段 2：连接电视、读身份、判资格、建工作目录"); common(s)
    s.add_argument("target", help="电视 IP 或 IP:端口"); s.add_argument("--wait", type=int, default=90, help="等授权秒数"); s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("series", help="手动指定系列（候选多于一个时）"); common(s); s.add_argument("--name", required=True); s.set_defaults(fn=cmd_series)
    s = sub.add_parser("backup", help="阶段 3：备份"); common(s); s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("plan", help="阶段 5：分组排批"); common(s)
    s.add_argument("--mode", choices=["safe", "advanced"], default="safe"); s.add_argument("--batch-size", type=int, default=10); s.add_argument("--show-unknown", action="store_true")
    s.add_argument("--confirm-guessed", action="store_true", help="用户已确认按包名猜测的垃圾可以停"); s.add_argument("--include", help="用户勾选的 ask 组包名，逗号分隔"); s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("dryrun", help="阶段 4：回滚预演，测出这台机能用哪条命令"); common(s)
    s.add_argument("--pkg"); s.add_argument("--allow-uninstall", action="store_true"); s.set_defaults(fn=cmd_dryrun)
    s = sub.add_parser("batch", help="阶段 6：停一批（≤10）"); common(s)
    s.add_argument("--n", type=int, default=1); s.add_argument("--pkgs", help="逗号分隔的包名，覆盖 --n"); s.add_argument("--label"); s.set_defaults(fn=cmd_batch)
    s = sub.add_parser("verify", help="阶段 7：真重启验证"); common(s); s.add_argument("--timeout", type=int, default=240); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("status", help="不重启，只看电视现状"); common(s); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("recover", help="阶段 8：黑屏固定流程"); common(s); s.set_defaults(fn=cmd_recover)
    s = sub.add_parser("restore", help="还原：默认最近一批"); common(s); s.add_argument("--all", action="store_true"); s.add_argument("--pkg"); s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("report", help="阶段 9：报告 + 结果卡"); common(s); s.add_argument("--mem-after", type=int); s.add_argument("--card", action="store_true"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("resume", help="看进行中的清理到哪一步了"); common(s); s.set_defaults(fn=cmd_resume)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except AdbNotFound as e:
        print(str(e), file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
