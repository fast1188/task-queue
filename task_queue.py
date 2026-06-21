"""task_queue.py — 任务排队 CLI v0.1

把要跑的命令 (shell / python / 任意) 排队, FIFO + 优先级, 支持 retry.
0 第三方依赖. 单文件 ~200 行.

用法:
    py task_queue.py add "echo hello" --name "test"        # 入队
    py task_queue.py list [--status pending]                # 列
    py task_queue.py run                                    # 顺序跑完所有 pending
    py task_queue.py run --once                             # 跑 1 个就退出
    py task_queue.py retry <id>                             # 失败重试
    py task_queue.py rm <id>                                # 删
    py task_queue.py status                                 # 总览
    py task_queue.py clear --status done                    # 清 done

存储: ~/.task_queue/queue.json (单文件 JSON 数组)
并发: 文件锁 (fcntl) + 原子写 (.tmp + rename)
"""
import argparse
import json
import os
import subprocess
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path

# fcntl 是 Unix only, Windows 用不到 (单进程假设)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

# Windows GBK stdout 兜底
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

QUEUE_DIR = Path.home() / ".task_queue"
QUEUE_FILE = QUEUE_DIR / "queue.json"
LOCK_FILE = QUEUE_DIR / "queue.lock"

VALID_STATUS = ("pending", "running", "done", "failed")


def ensure_dir():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


def load_queue():
    """读 queue.json, 文件不存在返回空 list"""
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_queue_atomic(items):
    """原子写: tmp + rename (避免写到一半被读)"""
    ensure_dir()
    fd, tmp_path = tempfile.mkstemp(dir=QUEUE_DIR, prefix=".queue_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, QUEUE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def with_lock(fn):
    """文件锁装饰: 避免并发写"""
    ensure_dir()
    if not HAS_FCNTL:
        return fn()  # Windows: 单进程假设
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        return fn()
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()


def next_id(items):
    return max((i["id"] for i in items), default=0) + 1


def cmd_add(args):
    def _do():
        items = load_queue()
        nid = next_id(items)
        item = {
            "id": nid,
            "name": args.name or f"task-{nid}",
            "cmd": args.command,
            "priority": args.priority,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "retries": 0,
            "max_retries": args.retries,
        }
        items.append(item)
        save_queue_atomic(items)
        print(f"✓ added #{nid}  {item['name']}  priority={item['priority']}")
        return 0
    return with_lock(_do)


def cmd_list(args):
    items = load_queue()
    if args.status:
        items = [i for i in items if i["status"] == args.status]
    if not items:
        print("(empty)")
        return 0
    # 排序: priority desc, id asc
    items.sort(key=lambda x: (-x["priority"], x["id"]))
    print(f"{'ID':<5} {'STATUS':<10} {'PRI':<4} {'RETRIES':<8} NAME  CMD")
    for i in items:
        print(f"{i['id']:<5} {i['status']:<10} {i['priority']:<4} {i['retries']:<8} {i['name']}  {i['cmd']}")
    return 0


def cmd_status(_args):
    items = load_queue()
    by = {s: 0 for s in VALID_STATUS}
    for i in items:
        by[i["status"]] = by.get(i["status"], 0) + 1
    print("Task queue status:")
    for s, n in by.items():
        print(f"  {s:<10} {n}")
    print(f"  {'TOTAL':<10} {len(items)}")
    print(f"  file: {QUEUE_FILE}")
    return 0


def cmd_rm(args):
    def _do():
        items = load_queue()
        before = len(items)
        items = [i for i in items if i["id"] != args.id]
        if len(items) == before:
            print(f"✗ no task with id={args.id}")
            return 1
        save_queue_atomic(items)
        print(f"✓ removed #{args.id}")
        return 0
    return with_lock(_do)


def cmd_clear(args):
    if not args.status:
        print("✗ --status required (e.g. --status done)")
        return 1
    def _do():
        items = load_queue()
        before = len(items)
        items = [i for i in items if i["status"] != args.status]
        save_queue_atomic(items)
        print(f"✓ cleared {before - len(items)} {args.status} tasks")
        return 0
    return with_lock(_do)


def run_one(item, verbose=False):
    """跑 1 个 task, 返回 (success, error_msg)"""
    if verbose:
        print(f"  [{item['id']}] running: {item['cmd']}")
    try:
        r = subprocess.run(
            item["cmd"],
            shell=True,
            timeout=item.get("timeout", 3600),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )
        if r.returncode == 0:
            return True, ""
        return False, f"exit {r.returncode}: {(r.stderr or '')[:200]}"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def cmd_run(args):
    """跑 pending queue"""
    once = args.once
    def _do():
        items = load_queue()
        ran = 0
        for item in items:
            if item["status"] != "pending":
                continue
            # 标记 running
            for i in items:
                if i["id"] == item["id"]:
                    i["status"] = "running"
                    i["started_at"] = datetime.now().isoformat(timespec="seconds")
            save_queue_atomic(items)

            ok, err = run_one(item, verbose=True)
            # 更新结果
            items = load_queue()
            for i in items:
                if i["id"] == item["id"]:
                    i["finished_at"] = datetime.now().isoformat(timespec="seconds")
                    if ok:
                        i["status"] = "done"
                    elif i["retries"] < i["max_retries"]:
                        i["status"] = "pending"
                        i["retries"] += 1
                        i["last_error"] = err
                    else:
                        i["status"] = "failed"
                        i["last_error"] = err
            save_queue_atomic(items)
            ran += 1
            if once:
                break
        if ran == 0:
            print("(no pending tasks)")
        else:
            print(f"\n✓ ran {ran} task(s)")
        return 0
    return with_lock(_do)


def cmd_retry(args):
    def _do():
        items = load_queue()
        for i in items:
            if i["id"] == args.id:
                if i["status"] not in ("failed", "done"):
                    print(f"✗ task #{args.id} status={i['status']}, only failed/done can retry")
                    return 1
                i["status"] = "pending"
                i["retries"] = 0
                i.pop("last_error", None)
                save_queue_atomic(items)
                print(f"✓ task #{args.id} reset to pending")
                return 0
        print(f"✗ no task with id={args.id}")
        return 1
    return with_lock(_do)


def main():
    ap = argparse.ArgumentParser(description="Task Queue CLI v0.1")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="入队新 task")
    p_add.add_argument("command", help="shell command")
    p_add.add_argument("--name", help="任务名 (默认 task-N)")
    p_add.add_argument("--priority", type=int, default=5, help="1-10, 高先跑 (默认 5)")
    p_add.add_argument("--retries", type=int, default=2, help="失败重试次数 (默认 2)")
    p_add.set_defaults(fn=cmd_add)

    p_list = sub.add_parser("list", help="列 task")
    p_list.add_argument("--status", choices=VALID_STATUS, help="过滤状态")
    p_list.set_defaults(fn=cmd_list)

    p_run = sub.add_parser("run", help="跑 pending 队列")
    p_run.add_argument("--once", action="store_true", help="只跑 1 个就退出 (daemon 用)")
    p_run.set_defaults(fn=cmd_run)

    p_retry = sub.add_parser("retry", help="重试 failed task")
    p_retry.add_argument("id", type=int, help="task id")
    p_retry.set_defaults(fn=cmd_retry)

    p_rm = sub.add_parser("rm", help="删 task")
    p_rm.add_argument("id", type=int, help="task id")
    p_rm.set_defaults(fn=cmd_rm)

    p_status = sub.add_parser("status", help="总览")
    p_status.set_defaults(fn=cmd_status)

    p_clear = sub.add_parser("clear", help="清某状态 task")
    p_clear.add_argument("--status", required=True, choices=VALID_STATUS)
    p_clear.set_defaults(fn=cmd_clear)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
