"""品牌入口库读取与匹配。数据真源 = data/brands/*.json（网页与 Skill 共用同一份）。"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


@lru_cache(maxsize=1)
def load_brands() -> list[dict]:
    items = []
    for path in sorted((DATA / "brands").glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        d["_file"] = path.stem
        items.append(d)
    return items


@lru_cache(maxsize=1)
def load_generic_rules() -> dict:
    with open(DATA / "generic-rules.json", encoding="utf-8") as fh:
        return json.load(fh)


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_/·（）()]", "", (s or "").lower())


def brand_tokens(brand: dict) -> list[str]:
    """品牌名去掉括号里的说明后按 / 拆开，加上别名；括号里常是「含 XX 贴牌机」这种会串品牌的话，不当 token。"""
    name = re.sub(r"[（(][^）)]*[）)]", "", brand["brand"])
    tokens = [name] + list(brand.get("aliases", []))
    out = []
    for t in tokens:
        for piece in re.split(r"[/、,，]", t):
            piece = re.sub(r"[（(][^）)]*[）)]", "", piece).strip()
            if len(_norm(piece)) >= 2:
                out.append(piece)
    return out


def match_brand(text: str) -> list[dict]:
    """用品牌名/别名/getprop 字段做模糊匹配。打分分三档：完全相等 > 别名出现在文本里 > 文本是别名的一部分。"""
    hay = _norm(text)
    scored = []
    for b in load_brands():
        best = 0
        for tok in brand_tokens(b):
            n = _norm(tok)
            if not n:
                continue
            if n == hay:
                best = max(best, 1000 + len(n))
            elif n in hay:
                best = max(best, 100 + len(n))
            elif len(hay) >= 2 and hay in n:
                best = max(best, len(hay))
        if best:
            scored.append((best, b))
    scored.sort(key=lambda x: -x[0])
    return [b for _, b in scored]


_VER_RE = re.compile(r"(\d+)(?:\.(\d+))?")


def version_in_range(release: str | None, rng: str | None) -> bool | None:
    """粗匹配安卓版本是否落在系列描述的范围里。None = 无法判断。"""
    if not release or not rng:
        return None
    m = _VER_RE.search(release)
    if not m:
        return None
    major = int(m.group(1))
    nums = [int(x) for x in re.findall(r"\b(\d{1,2})(?:\.\d+)?\b", rng)]
    nums = [n for n in nums if 4 <= n <= 16]
    if not nums:
        return None
    if len(nums) == 1:
        return major == nums[0]
    return min(nums) <= major <= max(nums)


def candidate_series(brand: dict, release: str | None = None, base_os: str | None = None) -> list[dict]:
    """给一个品牌，按安卓版本与底层系统筛系列；筛不出就全部返回，让 agent 问用户。"""
    series = brand.get("series", [])
    if base_os:
        filtered = [s for s in series if s.get("base_os") == base_os]
        if filtered:
            series = filtered
    if release:
        hits = [s for s in series if version_in_range(release, s.get("android_version_range")) is True]
        if hits:
            return hits
    return series


def series_packages(series: dict, key: str) -> list[dict]:
    return list(series.get(key, []) or [])


def compile_patterns(rules: list[dict], field: str = "pattern") -> list[tuple[re.Pattern, dict]]:
    return [(re.compile(r[field], re.I), r) for r in rules]


def pkg_glob_to_regex(pkg: str) -> re.Pattern:
    """品牌数据里的包名支持 com.x.* 与 *Name* 两种通配。"""
    if pkg.startswith("*") and pkg.endswith("*"):
        return re.compile(re.escape(pkg.strip("*")), re.I)
    if pkg.endswith(".*"):
        return re.compile("^" + re.escape(pkg[:-2]) + r"(\..+)?$", re.I)
    if pkg.endswith("*"):
        return re.compile("^" + re.escape(pkg[:-1]), re.I)
    return re.compile("^" + re.escape(pkg) + "$", re.I)
