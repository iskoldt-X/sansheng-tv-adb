#!/usr/bin/env python3
"""校验 data/brands/*.json：schema、来源非空、包名格式、禁停/垃圾不重叠、不适用品牌无 entries。
用法：python3 scripts/validate_data.py [--strict]  （exit 2 = 有错）"""
import json, re, sys, glob, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = json.load(open(os.path.join(ROOT, 'data', 'schema.json'), encoding='utf-8'))
PKG_RE = re.compile(r'^\*?[a-zA-Z][\w]*(\.[\w]+)*(\.\*|\*)?$')
try:
    import jsonschema
except ImportError:
    jsonschema = None

def check(path):
    errs, warns = [], []
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        return [f'JSON 解析失败: {e}'], []
    if jsonschema:
        v = jsonschema.Draft7Validator(SCHEMA)
        for e in sorted(v.iter_errors(d), key=lambda e: list(e.path)):
            errs.append(f'schema: {"/".join(str(p) for p in e.path)}: {e.message[:160]}')
    else:
        warns.append('jsonschema 未安装，只做语法与自定义检查')
    if d.get('status') == '不适用':
        if not d.get('not_applicable', {}).get('reason'):
            errs.append('不适用品牌缺 not_applicable.reason')
        for s in d.get('series', []):
            if s.get('entries'):
                errs.append(f'不适用品牌 series「{s.get("name")}」不应有 entries')
    for s in d.get('series', []):
        for e in s.get('entries', []):
            if not e.get('sources') and not (e.get('evidence_level') == '本机实测' and e.get('verified_by')):
                errs.append(f'series「{s.get("name")}」某入口缺 sources')
            if not e.get('steps'):
                errs.append(f'series「{s.get("name")}」某入口缺 steps')
            if e.get('evidence_level') == '本机实测' and not e.get('verified_by'):
                warns.append(f'series「{s.get("name")}」本机实测入口缺 verified_by')
        keep = {p['pkg'] for p in s.get('must_keep', [])}
        junk = {p['pkg'] for p in s.get('junk', [])}
        ask = {p['pkg'] for p in s.get('ask_user', [])}
        for name, group in (('must_keep', keep), ('junk', junk), ('ask_user', ask)):
            for pkg in group:
                if not PKG_RE.match(pkg):
                    errs.append(f'{name} 包名格式可疑: {pkg}')
        if keep & junk:
            errs.append(f'series「{s.get("name")}」must_keep 与 junk 重叠: {sorted(keep & junk)}')
        if keep & ask:
            errs.append(f'series「{s.get("name")}」must_keep 与 ask_user 重叠: {sorted(keep & ask)}')
        for group in ('must_keep', 'junk', 'ask_user'):
            for p in s.get(group, []):
                if not p.get('sources') and p.get('evidence_level') != '本机实测':
                    warns.append(f'{group} {p["pkg"]} 无来源')
        if not s.get('identify_hints'):
            warns.append(f'series「{s.get("name")}」缺 identify_hints（读者认不出自己）')
    return errs, warns

def main():
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'brands', '*.json')))
    if not files:
        print('没有品牌文件'); sys.exit(2)
    bad = 0
    for f in files:
        errs, warns = check(f)
        tag = '❌' if errs else '✅'
        print(f'{tag} {os.path.basename(f)}  错误 {len(errs)}  警告 {len(warns)}')
        for e in errs: print('   E', e)
        for w in warns[:12]: print('   W', w)
        if len(warns) > 12: print(f'   … 另 {len(warns)-12} 条警告')
        bad += bool(errs)
    sys.exit(2 if bad else 0)

if __name__ == '__main__':
    main()
