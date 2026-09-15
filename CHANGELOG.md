# Changelog

格式遵循 Keep a Changelog；版本号遵循 SemVer。

## [未发布]

- README「30 秒判断」表改成两列：粗体一句结论 + 小字机型清单，GitHub 上不再挤成一团。
- README（中英）加三张截图：假 adb 回放海信 2017 夹具的终端记录、网页入口库判定卡、海信品牌卡入口步骤（`assets/`）。

## [0.1.0] -- 2026-09-15

- 安全闸 G7 增加「写盘等待」：改完停用状态 20 秒内不重启（模拟器实测立刻重启会丢状态），并用 uptime 证明真重启。
- 在 Android 12 电视模拟器（API 31）上跑通 probe → backup → dryrun → plan → batch → verify → recover → restore → report 全链；发现该系统 `pm hide` 需 MANAGE_USERS、shell 不可用，`disable-user` 与 `uninstall -k --user 0` 可用。
- 通用规则补 Google 服务 / Play / 权限控制器 / 网络栈 / 遥控服务的禁停模式，补推荐行 / 屏保 / 反馈上报的垃圾提示与流媒体应用的「问用户」组。

- 首版：22 个品牌 / 81 个系列的开发者模式入口库（含证据等级与来源）、十二道安全闸的阶段机脚本（probe / backup / dryrun / plan / batch / verify / recover / restore / report）、中英文提示词、假 adb 回放测试（海信 2017 / 老联想 4.0.4 / 通用安卓 9 / 酷开 hide 被恢复四种 profile）。
