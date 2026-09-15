# 怎么投稿一台电视

最有价值的投稿是**你自己在这台机上走通的入口 + 哪条命令能用 + 前后数字**。三种方式，按省事程度排：

1. **开一个 issue**（最省事）：用「投稿一台电视的入口」表单填，我合进数据。
2. **改数据 PR**：编辑 `data/brands/<brand>.json`，按 `data/schema.json` 的字段填；没有的品牌新建文件。每条入口必须带 `sources`（URL + 标题）和 `evidence_level`。提交前跑：
   ```bash
   python3 scripts/validate_data.py      # schema / 来源 / 包名格式 / 禁停与垃圾不重叠
   python3 scripts/build_public.py       # 重新生成 references/entry-by-brand.md 与 dist/
   ```
3. **改脚本 PR**：加测试到 `tests/test_flow.py`（假 adb 在 `tests/mock_adb.py`，可以加新 profile），`python3 tests/test_flow.py` 全绿再提。

证据等级怎么标：自己实机做过 = `本机实测`（`verified_by` 写机型和日期）；厂商 / 官方文档 = `官方`；两个以上独立来源一致 = `多源`；只有一个帖子 = `单源`。拿不准就标低不标高。

不收的内容：root / 解锁 bootloader / 刷机方法；没有来源的包名清单；把别的品牌的型号串进来（同一位作者一稿两贴的帖子很常见，先核型号）。
