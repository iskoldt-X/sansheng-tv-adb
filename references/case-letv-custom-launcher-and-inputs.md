# 实战复盘 · 乐视 Android 12 电视更换极简 Apple TV 启动器与「信号源」攻坚记

> **案例背景**：一台 2024 款乐视 85 吋巨幕电视（Q85，CV560 板卡，海思方案，Android 12），在彻底清理底层开机视频广告后，原厂桌面依然充斥各类影视推荐与冗余信息。我们尝试为其更换开源的 CleanYourTV（tvOS / Apple TV 极简风格启动器），期间攻克了**「主页键被原厂桌面抢占」**、**「国内 TV 应用不显示」**以及最致命的**「遥控器信号源实体按键失效、电视无法切 HDMI」**三大难题。

---

## 一、 为什么选择极简启动器？

电视的核心本质是“显示设备”，对于接了主机、外置盒子或只看特定几个 App 的家庭来说，原厂桌面动辄几百兆的内存占用、强制瀑布流推荐和弹窗是极大的负担。

我们引入了社区基于原生 Java + AndroidX 开发的极简桌面 **CleanYourTV**（包名 `com.gathin.tvhome`）：
- 安装体积仅约几百 KB，零后台常驻网络轮询，纯黑底色防烧屏。
- 具有 Apple TV 般的 Dock 栏与光晕悬浮交互，右上角整合了快捷胶囊。
- 内置 `ClockDream` 极简漂浮时钟屏保。

---

## 二、 遇到的三大拦路虎

在初次编译并推送到乐视 Q85 电视上运行后，表面上看到了极简桌面，但在实际交互中遇到了严重的体验与功能死穴：

### 1. 拦路虎一：按遥控器【Home】主页键，强制跳回乐视原厂桌面
虽然通过 `cmd package set-home-activity com.gathin.tvhome/.MainActivity` 设置了默认桌面，但只要按下遥控器物理 Home 键，电视依旧无脑切回乐视官方桌面（`com.stv.deskplatform`）。
- **原因**：国内定制 TV 系统的窗口管理或遥控器驱动，对自家启动器存在硬编码式的唤醒优先级，只要原厂桌面存在，单靠普通的 `set-home-activity` 无法彻底接管。

### 2. 拦路虎二：桌面只显示 2 个系统 App，其余应用全空
 CleanYourTV 安装后，桌面应用网格里只有“系统更新”和“System Tracing”，电视自带的文件管理器、应用中心等全都不见。
- **原因**：CleanYourTV 作为海外开源项目，源码里只查询了 Google Android TV 标准的 `CATEGORY_LEANBACK_LAUNCHER`。而国内绝大多数电视预装 App 和第三方影视软件（包括当贝、爱腾芒 TV 版），Manifest 中声明的都是移动端标准的 `CATEGORY_LAUNCHER`。在 Android 11+ 的软件包可见性（Package Visibility）机制下，所有普通 App 都被系统默默过滤隐藏了。

### 3. 拦路虎三（最致命）：遥控器【信号源】按键失效，无法切 HDMI
在极简桌面上，按下遥控器上的实体【信号源】按键，屏幕毫无反应；且桌面右上角原本应该有的【输入源（Inputs）】胶囊按钮也离奇失踪！
对于经常需要切换 Switch、PS5、电视盒子或笔记本的用户来说，**不能切换信号源等于整台电视瘫痪了半边**。

---

## 三、 深度逆向：为什么信号源会失效？

为了弄清为什么只有乐视原厂桌面能切信号源，我们直接将乐视原厂桌面（`/system/app/com.stv.deskplatform/com.stv.deskplatform.apk`）和系统设置（`/system/app/CtvSetting/CtvSetting.apk`）提取到本地进行反编译与字符串链路排查：

### 1. 原厂桌面“私吞”了按键
在乐视原厂桌面 `classes.dex` 中，`Launcher.java` 在其窗口的 `dispatchKeyEvent` 阶段直接私自拦截了遥控器的物理信号源按键！
也就是说，遥控器的“信号源”按键在底层并不是由 Android 全局无条件分发的，而是**由前台原厂桌面吃进按键后，由桌面代码去调起信号源界面**。一旦换成了第三方启动器，该按键直接被新桌面当成未知输入抛弃，产生“按键失灵”的假象。

### 2. 国际标准与国产板卡的巨大鸿沟
海外 Google TV 项目（如 `cleanyourtv`、`free-my-tv`）全部依赖 Android TV 标准的 `TvInputManager` 来枚举 HDMI 端口并提供切换。
然而，海思方案 + Cultraview 硬件板卡在固件底层**根本没有向系统 TvInputManager 注册标准的透传通道**。CleanYourTV 在启动时检测到 `TvInputManager` 没有任何 HDMI 输入，误以为当前设备是一台没有 HDMI-In 的“外接流媒体盒子”，于是主动执行了 `btnInputs.setVisibility(View.GONE)` 将右上角按钮隐藏！

### 3. 抓出乐视底层的真正信号源入口
通过对 `CtvSetting.apk` 的 AndroidManifest.xml 以及原厂桌面调用链深入排查，我们锁定了乐视海思底层真正的信号源浮窗组件：
* **目标包名**：`com.cultraview.setting`
* **目标组件**：`com.cultraview.setting.SourceActivity`
* **对应 Action**：`com.android.tv.action.VIEW_INPUTS`

在终端执行：
```bash
adb shell am start -n com.cultraview.setting/.SourceActivity
```
屏幕中央立即弹出了原生熟悉的半透明蓝色浮窗——**【高清1】、【高清2】、【高清3】**整齐排列，方向键切换顺畅，按返回键瞬间关闭！证实该 Activity 正是乐视底层最核心的硬件信号源切换中枢！

---

## 四、 源码级改造与落地解决方案

搞清机理后，我们在 CleanYourTV 源码基础上进行了三大针对性改造：

### 1. 实体遥控器按键全局拦截
在 `MainActivity.java` 的 `dispatchKeyEvent` 以及无障碍后台服务 `InputKeyService.java` 中，增加对物理信号源按键（`KEYCODE_TV_INPUT=178`、`KEYCODE_TV=170` 等）的拦截：
```java
private static boolean isInputKey(int keyCode) {
    return keyCode == KeyEvent.KEYCODE_TV
            || keyCode == KeyEvent.KEYCODE_TV_INPUT
            || keyCode == 178
            || keyCode == KeyEvent.KEYCODE_AVR_INPUT
            || keyCode == KeyEvent.KEYCODE_WINDOW
            || keyCode == KeyEvent.KEYCODE_PROG_RED;
}
```
一旦捕获上述任意物理按键的 `ACTION_UP`，立刻唤起乐视原生组件：
```java
Intent intent = new Intent();
intent.setComponent(new ComponentName("com.cultraview.setting", "com.cultraview.setting.SourceActivity"));
intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
startActivity(intent);
```

### 2. 桌面右上角常驻【输入源】胶囊按钮
在 `MainActivity.java` 中修改 `updateInputsButton()`，强制：
```java
btnInputs.setVisibility(View.VISIBLE);
btnInputs.setOnClickListener(v -> openSourceMenu());
```
即便遥控器按键出现物理损坏或未对准红外，用户只需按遥控器【上】，在右上角点击【输入源】胶囊，同样秒弹信号源菜单，双重兜底。

### 3. 补齐国内 TV 应用生态支持
- 在 `AndroidManifest.xml` 的 `<queries>` 声明中补充 `<category android:name="android.intent.category.LAUNCHER" />` 以及 `com.cultraview.setting`、`com.cultraview.filemanager` 等系统包名。
- 在 `AppAdapter.java` 中将 `CATEGORY_LEANBACK_LAUNCHER` 与 `CATEGORY_LAUNCHER` 查询结果自动合并去重，让国内各类电视 App 和系统自带管理工具全量呈现在桌面网格中。

### 4. 彻底接管 Home 键
编译新 APK 覆盖安装后，执行一条停用命令彻底解决原厂桌面抢占：
```bash
adb shell pm disable-user --user 0 com.stv.deskplatform
```
由于系统内只剩下一个 Home 启动器，无论是开机还是按下遥控器【Home】键，系统毫无悬念 100% 直达极简桌面。

---

## 五、 实测验证与效果

1. **界面表现**：
   - 右上角【输入源】、【无线网络】、【设置】三颗毛玻璃胶囊并列。
   - 底部 Dock 栏和抽屉成功拉出“文件管理器”、“万象搜索”、“系统更新”等国内应用。
2. **信号源实测**：
   - 无论在桌面上用光标点击【输入源】胶囊，还是直接按遥控器物理【信号源】按键，均能毫秒级呼出乐视原生【高清1 / 高清2 / 高清3】浮动窗口。
   - 遥控器左右选择、按确定键切入 HDMI、按返回键退出浮动菜单全部工作正常。
3. **主页键锁定**：
   - 在任意 App 或信号源画面按下遥控器【Home】键，干脆利落直达极简主页，原厂花哨广告桌面彻底隐退。

---

## 六、 安全回退与一键还原

如果未来需要换回乐视原厂桌面，无需重新刷机，只需通过电脑 ADB 执行：
```bash
# 1. 恢复乐视原厂桌面
adb shell pm enable com.stv.deskplatform

# 2. 重新指定默认桌面
adb shell cmd package set-home-activity com.stv.deskplatform/.ui.activity.ExternalJumpActivity

# 3. （可选）卸载极简桌面
adb uninstall com.gathin.tvhome
```
即刻完整复原。
