#!/usr/bin/env python3
"""V1 录像回放测试：用假 adb 把阶段机整条走一遍，断言安全闸真的会拦、真的会还原。

跑法：python3 tests/test_flow.py   （不需要电视，不需要真 adb）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLI = [sys.executable, str(ROOT / "scripts" / "tvadb.py")]


class Flow:
    def __init__(self, profile: str):
        self.tmp = Path(tempfile.mkdtemp(prefix=f"tvadb-{profile}-"))
        self.env = dict(os.environ, ADB=str(HERE / "mock_adb.py"), MOCK_TV_PROFILE=profile,
                        MOCK_TV_STATE=str(self.tmp / "mock_state.json"), TVADB_HOME=str(self.tmp / "home"))

    def run(self, *args: str, ok: bool = True) -> dict | str:
        proc = subprocess.run(CLI + list(args) + ["--json"], env=self.env, capture_output=True, text=True, timeout=300)
        if ok and proc.returncode != 0:
            raise AssertionError(f"{args} exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout + proc.stderr

    def text(self, *args: str) -> str:
        proc = subprocess.run(CLI + list(args), env=self.env, capture_output=True, text=True, timeout=300)
        return proc.stdout + proc.stderr

    def mock_state(self) -> dict:
        return json.loads((self.tmp / "mock_state.json").read_text())

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Hisense2017(unittest.TestCase):
    """09-14 实测的那台：disable-user 被封、hide 可用、第一批后黑屏一次。"""

    @classmethod
    def setUpClass(cls):
        cls.f = Flow("hisense2017")

    @classmethod
    def tearDownClass(cls):
        cls.f.cleanup()

    def test_01_probe_waits_for_auth_and_judges(self):
        info = self.f.run("probe", "192.168.1.34", "--wait", "30")
        self.assertEqual(info["status"], "device")
        self.assertEqual(info["sdk"], 22)
        self.assertEqual(info["eligibility"]["verdict"], "ok")
        self.assertIn("海信", info["brand_match"])
        self.assertTrue(self.f.mock_state()["auth_polls"] >= 3, "应当经历过 unauthorized 轮询再拿到 device")

    def test_02_plan_refuses_without_backup(self):
        out = self.f.run("plan", ok=False)
        self.assertIn("还没备份", str(out))

    def test_03_backup_then_plan_groups(self):
        m = self.f.run("backup")
        self.assertGreaterEqual(len(m), 10)
        plan = self.f.run("plan")
        keep = {e["pkg"] for e in plan["groups"]["keep"]}
        junk = {e["pkg"] for e in plan["groups"]["junk"]}
        self.assertIn("com.jamdeo.launcher", keep)
        self.assertIn("com.android.inputmethod.latin", keep)
        self.assertIn("com.hisense.service.ad", junk)
        self.assertFalse(keep & junk)
        self.assertTrue(all(len(b) <= 10 for b in plan["batches"]))

    def test_04_dryrun_finds_hide_only(self):
        res = self.f.run("dryrun")
        self.assertFalse(res["caps"]["disable_user"]["ok"])
        self.assertTrue(res["caps"]["hide"]["ok"])
        self.assertEqual(res["usable"], ["hide"])
        st = self.f.mock_state()
        self.assertEqual(st["hidden"], [], "预演后必须还原干净")

    def test_05_batch_refuses_keep_list(self):
        res = self.f.run("batch", "--pkgs", "com.jamdeo.launcher,com.hisense.service.ad", "--label", "混入禁停")
        self.assertEqual([s["pkg"] for s in res["skipped"]], ["com.jamdeo.launcher"])
        self.assertEqual([d["pkg"] for d in res["done"]], ["com.hisense.service.ad"])
        self.f.run("restore", "--all")
        self.assertEqual(self.f.mock_state()["hidden"], [])

    def test_06_batch_too_big_refused(self):
        out = self.f.run("batch", "--pkgs", ",".join(f"com.x.p{i}" for i in range(11)), ok=False)
        self.assertIn("最多 10 个", str(out))

    def test_07_first_batch_black_screen_then_recover(self):
        res = self.f.run("batch", "--n", "1")
        self.assertEqual(len(res["done"]), 10)
        v = self.f.run("verify", "--timeout", "20")
        self.assertTrue(v["screen_black"], "fixture 约定第一批后第一次真重启黑屏")
        self.assertFalse(v["ok"])
        rec = self.f.run("recover")
        last = rec["steps"][-1]["result"]
        self.assertFalse(last["screen_black"])
        self.assertEqual(self.f.mock_state()["hidden"], [], "recover 应把最近一批全部还原")
        # 重做一批并验证通过
        res = self.f.run("batch", "--n", "1", "--label", "第 1 批（重做）")
        v = self.f.run("verify", "--timeout", "20")
        self.assertTrue(v["ok"])
        self.assertEqual(v["still_off"], 10)

    def test_08_report_counts_only_active(self):
        rep = self.f.text("report", "--card")
        self.assertIn("| 停用的预装应用 | 0 | 10 个 |", rep)
        self.assertRegex(rep, r"结果卡：.*card\.(png|svg)")
        rb = (Path(self.f.env["TVADB_HOME"]).glob("*/rollback.sh"))
        text = next(rb).read_text()
        self.assertIn("pm unhide com.hisense.service.ad", text)

    def test_09_restore_all_returns_everything(self):
        res = self.f.run("restore", "--all")
        self.assertTrue(all(r["restored"] for r in res["results"]))
        self.assertEqual(self.f.mock_state()["hidden"], [])


class Lenovo404(unittest.TestCase):
    """2013 夏普×联想：无授权弹窗、Android 4.0.4、什么都停不了。"""

    def test_limited_verdict_and_no_usable_method(self):
        f = Flow("lenovo404")
        try:
            info = f.run("probe", "192.168.1.29", "--wait", "5")
            self.assertEqual(info["status"], "device")
            self.assertEqual(info["eligibility"]["verdict"], "limited")
            self.assertEqual(f.mock_state()["auth_polls"], 1, "老联想没有授权弹窗，第一次就该是 device")
            f.run("backup")
            f.run("plan")
            res = f.run("dryrun")
            self.assertEqual(res["usable"], [])
            out = f.run("batch", "--n", "1", ok=False)
            self.assertIn("回滚预演", str(out))
        finally:
            f.cleanup()


class Generic9(unittest.TestCase):
    def test_disable_user_preferred(self):
        f = Flow("generic9")
        try:
            f.run("probe", "192.168.1.50", "--wait", "30")
            f.run("backup"); f.run("plan", "--confirm-guessed")
            res = f.run("dryrun")
            self.assertEqual(res["usable"][:2], ["disable_user", "hide"])
            b = f.run("batch", "--n", "1")
            self.assertTrue(all(d["method"] == "disable_user" for d in b["done"]))
            v = f.run("verify", "--timeout", "20")
            self.assertTrue(v["ok"])
        finally:
            f.cleanup()


class CoocaaRevert(unittest.TestCase):
    """酷开式：hide 重启后被系统恢复 → verify 报 came_back → 允许 uninstall 预演后换方法。"""

    def test_hide_reverts_then_fallback(self):
        f = Flow("coocaa_revert")
        try:
            f.run("probe", "192.168.1.60", "--wait", "30")
            f.run("backup"); f.run("plan", "--confirm-guessed")
            res = f.run("dryrun")
            self.assertEqual(res["usable"], ["hide"])
            f.run("batch", "--n", "1")
            v = f.run("verify", "--timeout", "20")
            self.assertFalse(v["ok"])
            self.assertTrue(len(v["came_back"]) >= 1, "系统恢复了 hide，verify 必须发现")
            res = f.run("dryrun", "--allow-uninstall")
            self.assertTrue(res["caps"]["uninstall_user0"]["ok"])
            self.assertEqual(res["usable"], ["hide", "uninstall_user0"])
        finally:
            f.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
