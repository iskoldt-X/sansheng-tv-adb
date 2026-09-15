"""阶段 9：收尾报告 + 结果卡。

报告先给用户三样：备份在哪、回滚一句话、开发者模式留着不碍事；再给前后对比表。
结果卡是一张固定版式 PNG（没有 Pillow 就出 SVG），用户可以直接发给朋友。
"""
from __future__ import annotations

import re
from pathlib import Path

from .ops import active_done
from .session import Session

SITE = "sanshengai.top/tools/tv-adb"


def _mem_before(sess: Session) -> int | None:
    p = sess.dir / "backup" / "proc_meminfo.txt"
    if not p.exists():
        return None
    m = re.search(r"MemAvailable:\s+(\d+)", p.read_text(encoding="utf-8")) or re.search(r"MemFree:\s+(\d+)", p.read_text(encoding="utf-8"))
    return round(int(m.group(1)) / 1024) if m else None


def build_report(sess: Session, mem_after: int | None = None) -> str:
    dev, st = sess.device, sess.state
    done = active_done(st)
    ver = (st.get("verifications") or [{}])[-1]
    mem_after = mem_after or ver.get("mem_available_mb")
    mem_before = _mem_before(sess)
    lines = [
        f"# 电视清理报告 · {dev.get('brand','')} {dev.get('model','')}",
        "",
        f"- 备份在：`{sess.dir / 'backup'}`",
        f"- 想全部还原：先 `adb connect {st.get('target','<电视IP>:5555')}`，再 `sh {sess.rollback}`",
        "- 开发者模式不用关，留着不碍事；以后想恢复某个功能，一句话就能做",
        "",
        "| 项目 | 清理前 | 清理后 |",
        "|---|---|---|",
        f"| 可用内存 | {mem_before or '?'} MB | {mem_after or '?'} MB |",
        f"| 停用的预装应用 | 0 | {len(done)} 个 |",
        "| 卸载的系统应用 | 0 | 0 |",
        "| root | 无 | 无 |",
        "",
        f"系统 Android {dev.get('android_release','?')}（{dev.get('base_os','android')}），总内存 {dev.get('mem_total_mb','?')} MB，共 {dev.get('package_count','?')} 个应用。",
        "",
        "## 停用清单",
    ]
    for a in done:
        lines.append(f"- `{a['pkg']}`（{a['method']}）→ 还原：`{a['restore'][0]}`")
    if not done:
        lines.append("- （没有停用任何应用）")
    lines += ["", f"入口库与提示词：{SITE}", ""]
    text = "\n".join(lines)
    (sess.dir / "report.md").write_text(text, encoding="utf-8")
    return text


def build_card(sess: Session, mem_after: int | None = None) -> Path:
    dev, st = sess.device, sess.state
    done = active_done(st)
    ver = (st.get("verifications") or [{}])[-1]
    mem_after = mem_after or ver.get("mem_available_mb") or 0
    mem_before = _mem_before(sess) or 0
    title = f"{dev.get('brand','')} {dev.get('model','')}".strip() or "我的电视"
    gain = max(mem_after - mem_before, 0)
    W, H = 1080, 600
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception:
        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}'>
<rect width='100%' height='100%' fill='#f6faf8'/>
<text x='60' y='110' font-size='34' fill='#8a958f' font-family='sans-serif'>{title}</text>
<text x='60' y='230' font-size='120' font-weight='700' fill='#0E926F' font-family='sans-serif'>+{gain} MB</text>
<text x='60' y='300' font-size='30' fill='#333' font-family='sans-serif'>可用内存 {mem_before} MB → {mem_after} MB</text>
<text x='60' y='360' font-size='30' fill='#333' font-family='sans-serif'>停用 {len(done)} 个预装应用 · 未卸载 · 未 root · 每步可还原</text>
<text x='60' y='540' font-size='26' fill='#8a958f' font-family='sans-serif'>{SITE}</text>
</svg>"""
        p = sess.dir / "card.svg"
        p.write_text(svg, encoding="utf-8")
        return p
    img = Image.new("RGB", (W, H), "#f6faf8")
    d = ImageDraw.Draw(img)

    def font(size: int, bold: bool = False):
        for cand in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                continue
        return ImageFont.load_default()

    d.text((60, 70), title, fill="#8a958f", font=font(34))
    d.text((60, 130), f"+{gain} MB", fill="#0E926F", font=font(128, True))
    d.text((60, 300), f"可用内存 {mem_before} MB → {mem_after} MB", fill="#333333", font=font(32))
    d.text((60, 352), f"停用 {len(done)} 个预装应用 · 未卸载 · 未 root · 每步可还原", fill="#333333", font=font(30))
    d.rectangle([60, 470, 1020, 472], fill="#dce8e2")
    d.text((60, 500), SITE, fill="#8a958f", font=font(26))
    p = sess.dir / "card.png"
    img.save(p)
    return p
