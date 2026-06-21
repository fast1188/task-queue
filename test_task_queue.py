"""test_task_queue.py — task_queue v0.1 单元测试 (5 个)
跑法: python -X utf8 -m unittest test_task_queue -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import task_queue as tq  # noqa: E402


class TestAdd(unittest.TestCase):
    """add command: 入队 + 优先级"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tq-test-"))
        self._patcher = patch.object(tq, "QUEUE_DIR", self.tmp)
        self._patcher.start()
        self._patcher2 = patch.object(tq, "QUEUE_FILE", self.tmp / "queue.json")
        self._patcher2.start()

    def tearDown(self):
        self._patcher.stop()
        self._patcher2.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_basic(self):
        """add 1 个 task, 出现在 list"""
        from argparse import Namespace
        args = Namespace(command="echo hello", name="greet", priority=5, retries=2)
        rc = tq.cmd_add(args)
        self.assertEqual(rc, 0)
        items = tq.load_queue()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "greet")
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["id"], 1)

    def test_add_priority_ordering(self):
        """priority 高的在 list 中靠前"""
        from argparse import Namespace
        tq.cmd_add(Namespace(command="low", name="L", priority=1, retries=0))
        tq.cmd_add(Namespace(command="high", name="H", priority=9, retries=0))
        tq.cmd_add(Namespace(command="mid", name="M", priority=5, retries=0))
        items = tq.load_queue()
        # list 排序: priority desc, id asc
        items.sort(key=lambda x: (-x["priority"], x["id"]))
        self.assertEqual([i["name"] for i in items], ["H", "M", "L"])


class TestRun(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tq-run-"))
        self._p1 = patch.object(tq, "QUEUE_DIR", self.tmp)
        self._p2 = patch.object(tq, "QUEUE_FILE", self.tmp / "queue.json")
        self._p1.start(); self._p2.start()

    def tearDown(self):
        self._p1.stop(); self._p2.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_success(self):
        from argparse import Namespace
        tq.cmd_add(Namespace(command=f"{sys.executable} -c \"print('ok')\"", name="ok", priority=5, retries=0))
        rc = tq.cmd_run(Namespace(once=True))
        self.assertEqual(rc, 0)
        items = tq.load_queue()
        self.assertEqual(items[0]["status"], "done")

    def test_run_failure_with_retry(self):
        from argparse import Namespace
        # 用一个必然失败的命令
        tq.cmd_add(Namespace(command="python -c \"import sys; sys.exit(1)\"", name="fail", priority=5, retries=2))
        rc = tq.cmd_run(Namespace(once=True))
        self.assertEqual(rc, 0)
        items = tq.load_queue()
        # 失败 1 次, retries=1, status 应回 pending (因为 retries < max_retries)
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["retries"], 1)
        self.assertIn("last_error", items[0])

    def test_run_failure_no_retry(self):
        from argparse import Namespace
        tq.cmd_add(Namespace(command="python -c \"import sys; sys.exit(1)\"", name="fail", priority=5, retries=0))
        rc = tq.cmd_run(Namespace(once=True))
        self.assertEqual(rc, 0)
        items = tq.load_queue()
        # retries=0, 立即 failed
        self.assertEqual(items[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
