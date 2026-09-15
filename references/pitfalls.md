# 实测与多源确认的坑

每条写清「症状 → 根因 → 脚本怎么防」。来源等级：本机实测 / 多源 / 官方。

## 待机不是重启（本机实测，2017 海信 VIDAA）
- 症状：停完一批让用户「关机再开」测试，一切正常；几批之后才突然黑屏。
- 根因：遥控器关机键是待机，系统没重新起来；停用引发的问题被掩盖到真重启时才暴露。
- 防：`verify` 只认 `adb reboot`；SKILL.md 阶段 7 固定说法里明说这一点。

## 第一次停用后系统自行重启、桌面黑屏一次（本机实测）
- 症状：停用广告包后几分钟 system_server 卡死被看门狗重启，重启后桌面进程在前台但内容区空白，只有音量条能弹。
- 根因：桌面开机时等某个被藏掉的组件握手，错过就一直空着；属于一次性状态。
- 事实：三个广告包放回去不行、十个全放回去也不行，真重启一次就好；之后逐组藏包每组真重启都正常。
- 防：`recover` 的固定顺序：查状态 → 还原最近一批 → 真重启 → 不行再全还原再重启。沟通第一句「电视没坏，系统在运行」。

## `pm disable-user` 被厂商封（本机实测海信 2017；多源：酷开 6.x、Fire OS 受保护包）
- 症状：`SecurityException: attempt to change component state ... uid=2000`。
- 根因：厂商裁掉了 shell 的组件状态权限（海信），或把特定包标为受保护（Fire OS 的 OTA / metrics / crashmanager / launcher）。
- 防：`dryrun` 逐级试 disable-user → hide → uninstall --user 0，只把预演过「停得掉且还得回来」的方法写进 `method_order`。

## 改完立刻重启，停用状态会丢（模拟器实测 Android 12；很可能就是「hide 重启后被恢复」的真身）
- 症状：`pm disable-user` 成功、`pm list packages -d` 也看得到，紧接着 `adb reboot`，重启后全部回到启用状态。
- 根因：包管理器把状态写进 `packages.xml` 有约 10 秒的延迟合并，`adb reboot` 不等它落盘。等 20 秒再重启就全部保住（同一台模拟器两次对照）。
- 防：`verify` 在最近一次改动后不足 20 秒时先等够再重启；重启后用 uptime 变小证明真的重启过，再复查停用状态。

## `pm hide` 重启后被系统恢复（多源，酷开 6.x 对 com.tianci.ad）
- 症状：停完验证正常，真重启后包又回来了。可能是上一条的写盘延迟，也可能是厂商守护进程在开机时恢复。
- 防：`verify` 复查 `came_back`；已经等过写盘仍被恢复的，才判定为厂商恢复，提示换下一级方法（需 `dryrun --allow-uninstall` 预演）。

## `pm hide` 在 Android 12 原生电视上要 MANAGE_USERS 权限（模拟器实测）
- 症状：`SecurityException: Neither user 2000 nor current process has android.permission.MANAGE_USERS`。
- 说明：shell 能用 `disable-user` 和 `uninstall -k --user 0`（还原用 `cmd package install-existing`），但 `hide` 不行；与 2017 海信正好相反。方法只能靠 `dryrun` 实测，不能靠版本号推。

## 同品牌不同代命令方向相反（多源，海信）
- 2017 款：disable-user 被封、hide 可用；HZ60U7A 一代：`pm uninstall -k --user 0` 可用。
- 防：`pm_caps` 只按系列生效，不跨代外推；`dryrun` 以实测为准。

## Android 4.x：连得上，改不了（本机实测，夏普 LCD-80LX850A = 联想 ideatv K82）
- 没有 `com.android.settings`，「开发者选项」根本不存在；adb 5555 出厂就开、没有授权弹窗。
- `pm disable` 拒绝、没有 `hide`、没有 `--user`；只有 /data/app 下后装包可卸。
- 防：`probe` 的 G1 资格闸给「limited」结论并停止；不去找 root。

## 误标的包名（多源）
- `com.amazon.firebat` 在流传很广的清单里写作「电池分析服务」，Fire OS 8.1.8.0 实机上是 Prime Video 本体。
- `com.mediatek.tv.oneworld.tvcenter` / `com.mediatek.wwtv.tvcenter` 是 HDMI 信号源切换器，不是「MediaTek 电视中心推广」。
- 索尼流传最广的 skinny-bravia 脚本用 `uninstall`，还把 `com.sony.dtv.tvx`（电视核心）、BRAVIA Sync、TalkBack/TTS 列进卸载清单——照抄会废掉信号源、HDMI-CEC 和读屏。
- 防：`generic-rules.json` 的禁停模式覆盖 tvcenter / wwtv / cec / talkback / tts；品牌 `must_keep` 收录这些包；SKILL.md 与网页都提醒「别照抄网上脚本」。

## 禁桌面变砖（多源，小米）
- Redmi X55 2022 款（MIUI TV 2.27.5224）禁 `com.mitv.tvhome` 后断电起不来；海外 Xiaomi TV Box S 2nd Gen 禁 Google TV 桌面后黑屏。
- 防：`launcher|tvhome|home` 在通用禁停模式里，脚本层跳过；老电视不换桌面写进红线。

## 临时停用忘了恢复（多源，TCL 国行）
- `pm disable-user com.android.packageinstaller` 后没 enable，重启开不了机；雷鸟雀系列卸掉工厂/重置组件后恢复出厂与工厂菜单全失效。
- 防：安装器与工厂/重置类在禁停模式里；`status` 会警告禁停名单里有包处于我们造成的停用状态。

## Google TV Android 13/14 的连接方式变了（多源）
- Android ≤12 打开 USB 调试即可 `adb connect IP:5555`；13/14 必须「无线调试」配对码 `adb pair IP:配对端口`，配对端口与连接端口是两个随机端口，重启后失效，插网线时无线调试开关会自动弹回关闭。
- 防：`probe` 连不上时提示 `adb pair`；`entry` 输出里 `wireless_pairing=true` 的系列要先走配对。

## 入口的前置条件（多源，TCL 灵控、康佳）
- TCL 灵控系统要先打开「商场模式」，ADB 开关才露出来。
- 康佳 / KKTV 近年蓝牙遥控器没有数字键，工厂密码 2008 输不进去，要外接蓝牙键盘。
- 防：数据 `entries[].prerequisites` 字段；`entry` 命令先打印前提。

## 华为智慧屏「能不能」看版本号不看年份（官方）
- HarmonyOS ≤4.x 保留安卓兼容层、有 ADB；MateTV（型号 EDIH/EDIJ）出厂即 HarmonyOS 5+，只有 HDC，官方确认不可回退。
- 荣耀智慧屏官方客服两次答复不支持 ADB；早期 X1 固件的 HDC 开关已在 2024 年升级后下线。

## 不适用系统的替代动作要说清「做不到什么」（官方）
- 三星 Tizen 的开发者模式只给 Tizen 应用侧载，碰不到系统应用。
- Vizio 关掉 Viewing Data 不等于停止采集：Activity Data 无独立开关，只能恢复出厂。
- 防：`not_applicable.alternative` 里两句话：能做到什么、做不到什么。
