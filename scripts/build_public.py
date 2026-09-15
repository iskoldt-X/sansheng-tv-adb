#!/usr/bin/env python3
"""把 data/ 编译成给人读和给网页用的派生物（勿手改派生物，改数据再重跑）：

  references/entry-by-brand.md   人读版入口总表（agent 与读者共用）
  dist/data.json                 合并后的品牌数据（网页 /tools/tv-adb/data.json 直接用）
  dist/prompt.txt / prompt.en.txt   提示词纯文本（网页与 agent 端点）
  dist/summary.json              统计（品牌数 / 系列数 / 入口数 / 证据等级分布）

用法：python3 scripts/build_public.py [--check]   --check 只比对不写盘（门禁用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from tvadb.data import load_brands  # noqa: E402

STATUS_ORDER = {"适用": 0, "部分适用": 1, "不适用": 2}


def prompt_text(md: str) -> str:
    """提示词文件里 `---` 之后的正文就是可复制的提示词。"""
    parts = md.split("\n---\n", 1)
    body = parts[1] if len(parts) == 2 else md
    return body.strip() + "\n"


def build_md(brands: list[dict]) -> str:
    out = ["# 各品牌开发者模式 / ADB 入口总表（由 data/brands 生成，勿手改）", ""]
    out.append("证据等级：本机实测 > 官方 > 多源 > 单源。「不适用」品牌只给替代动作。")
    for b in sorted(brands, key=lambda x: (STATUS_ORDER.get(x["status"], 9), x["_file"])):
        out += ["", f"## {b['brand']} · {b['status']}", ""]
        if b.get("summary"):
            out.append(b["summary"])
        ih = b.get("identify_hints") or {}
        if ih:
            out += ["", "**怎么认机型**", ""]
            for k, v in ih.items():
                if v:
                    out.append(f"- {k}：{v if isinstance(v, str) else '；'.join(v)}")
        if b["status"] == "不适用":
            na = b.get("not_applicable", {})
            out += ["", f"**为什么不适用**：{na.get('reason','')}", "", f"**替代动作**：{na.get('alternative','')}"]
        for s in b.get("series", []):
            out += ["", f"### {s['name']}（{s.get('years','')}，{s.get('base_os','')} {s.get('android_version_range','') or ''}）", ""]
            if s.get("identify_hints"):
                out.append(f"- 认：{s['identify_hints']}")
            if s.get("worth_it_hint"):
                out.append(f"- 值不值：{s['worth_it_hint']}")
            for e in s.get("entries", []):
                out += ["", f"**{e.get('applies_to','入口')}** · 证据：{e.get('evidence_level','')}"]
                for p in e.get("prerequisites", []) or []:
                    out.append(f"- 前提：{p}")
                for i, step in enumerate(e.get("steps", []), 1):
                    out.append(f"{i}. {step}")
                meta = []
                if e.get("has_auth_prompt") is not None:
                    meta.append("授权弹窗：" + ("有" if e["has_auth_prompt"] else "无"))
                if e.get("wireless_pairing"):
                    meta.append("需配对码")
                if e.get("port"):
                    meta.append(f"端口 {e['port']}")
                if meta:
                    out.append("- " + "；".join(meta))
                if e.get("risk_notes"):
                    out.append(f"- ⚠ {e['risk_notes']}")
                for src in e.get("sources", []):
                    out.append(f"- 来源：[{src.get('title','')}]({src.get('url','')}) {src.get('date','')}")
            caps = s.get("pm_caps") or {}
            if caps:
                out.append(f"- 命令能力：disable-user={caps.get('disable_user')} · hide={caps.get('hide')} · uninstall-user0={caps.get('uninstall_user0')}" + (f"（{caps.get('notes')}）" if caps.get("notes") else ""))
            for key, label in (("must_keep", "禁停"), ("junk", "可停"), ("ask_user", "看你用不用")):
                items = s.get(key) or []
                if items:
                    out.append(f"- {label}（{len(items)}）：" + "、".join(f"`{p['pkg']}`" for p in items[:40]) + ("…" if len(items) > 40 else ""))
            for p in s.get("pitfalls", []) or []:
                out.append(f"- 坑：{p.get('what','')}")
        if b.get("open_questions"):
            out += ["", "**待验证 · 欢迎投稿**", ""] + [f"- {q}" for q in b["open_questions"]]
    return "\n".join(out) + "\n"


def build_data(brands: list[dict]) -> dict:
    items = []
    for b in brands:
        d = {k: v for k, v in b.items() if not k.startswith("_")}
        d["id"] = b["_file"]
        items.append(d)
    items.sort(key=lambda x: (STATUS_ORDER.get(x["status"], 9), x["id"]))
    return {"generated_from": "sansheng-tv-adb/data/brands", "brands": items}


def build_summary(brands: list[dict]) -> dict:
    ev = Counter()
    n_series = n_entries = 0
    for b in brands:
        for s in b.get("series", []):
            n_series += 1
            for e in s.get("entries", []):
                n_entries += 1
                ev[e.get("evidence_level", "?")] += 1
    return {
        "brands": len(brands),
        "series": n_series,
        "entries": n_entries,
        "evidence": dict(ev),
        "status": dict(Counter(b["status"] for b in brands)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对派生物是否与数据一致，不写盘")
    ap.add_argument("--web-dir", default=os.environ.get("TVADB_WEB_DIR", ""), help="网站工程目录（个人网站/web）；给了就同时生成 src/data/tv-adb.json 与 public/tools/tv-adb/*")
    a = ap.parse_args()
    brands = load_brands()
    outputs = {
        ROOT / "references" / "entry-by-brand.md": build_md(brands),
        ROOT / "dist" / "data.json": json.dumps(build_data(brands), ensure_ascii=False, indent=1) + "\n",
        ROOT / "dist" / "prompt.txt": prompt_text((ROOT / "references" / "prompt-zh.md").read_text(encoding="utf-8")),
        ROOT / "dist" / "prompt.en.txt": prompt_text((ROOT / "references" / "prompt-en.md").read_text(encoding="utf-8")),
        ROOT / "dist" / "summary.json": json.dumps(build_summary(brands), ensure_ascii=False, indent=1) + "\n",
    }
    # 网站副本：只在显式给了 --web-dir（或 TVADB_WEB_DIR）时生成，公开仓用户不会碰到；
    # 本地维护者指向当前产品工作树的 个人网站/web，别指向主仓工作树。
    web = Path(a.web_dir).expanduser() if a.web_dir else None
    if web and web.is_dir():
        outputs[web / "src" / "data" / "tv-adb.json"] = outputs[ROOT / "dist" / "data.json"]
        outputs[web / "public" / "tools" / "tv-adb" / "data.json"] = outputs[ROOT / "dist" / "data.json"]
        outputs[web / "public" / "tools" / "tv-adb" / "prompt.txt"] = outputs[ROOT / "dist" / "prompt.txt"]
        outputs[web / "public" / "tools" / "tv-adb" / "prompt.en.txt"] = outputs[ROOT / "dist" / "prompt.en.txt"]
    stale = []
    for path, content in outputs.items():
        if a.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            try:
                rel = path.relative_to(ROOT)
            except ValueError:
                rel = path
            print(f"wrote {rel} ({len(content.encode('utf-8'))} bytes)")
    if a.check:
        if stale:
            print("派生物过期，需重跑 build_public.py：\n  " + "\n  ".join(stale))
            return 2
        print("派生物与数据一致")
    print(json.dumps(build_summary(brands), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
