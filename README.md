# task-queue · 任务排队 CLI

> Owner: fast118 · 2026-06-21 创建
> 范围：把 shell 命令 (echo / python / 任意) 排队跑，FIFO + 优先级 + retry
> 配套：30 天 60 计划 Day 9 A 档 (06-23 提前)

## 这是什么

单文件 Python CLI，**入队 → 跑 → 看状态 → retry**，全在终端里。

不像 Celery 那样需要 broker / worker / 进程，**就一个 JSON 文件 + 一个 daemon 命令**。

## 安装

```bash
git clone https://github.com/fast1188/task-queue.git
cd task-queue
# 0 依赖, Python 3.10+ 直接用
```

## 使用

```bash
# 入队 (id 自增)
py task_queue.py add "echo hello" --name "greet" --priority 5
# ✓ added #1  greet  priority=5

py task_queue.py add "python train.py" --name "ml-job" --priority 8 --retries 3
# ✓ added #2  ml-job  priority=8

# 列 (按 priority 倒序)
py task_queue.py list
# ID    STATUS     PRI  RETRIES  NAME     CMD
# 2     pending    8    0        ml-job   python train.py
# 1     pending    5    0        greet    echo hello

# 跑 pending (按 priority 倒序, FIFO 同优先级)
py task_queue.py run            # 跑完所有 pending
py task_queue.py run --once     # 跑 1 个就退出 (daemon 模式)

# 失败重试
py task_queue.py retry 2

# 状态总览
py task_queue.py status
# Task queue status:
#   pending     1
#   running     0
#   done        0
#   failed      0
#   TOTAL       1
#   file: C:\Users\Administrator\.task_queue\queue.json

# 删 / 清
py task_queue.py rm 1
py task_queue.py clear --status done
```

## 设计取舍

- **单文件 + JSON** — 不引入 broker / DB, 简单可读, 适合本地 1-100 任务
- **文件锁** — fcntl (Linux/Mac), Windows 退化单进程假设 (1 个跑 + 1 个写)
- **原子写** — `tempfile + os.replace` 避免写到一半被读
- **retry 计数** — 失败自动 re-pending, 达到 max_retries 才标 failed
- **优先级** — 1-10 整数, list 排序时 desc, run 时按 desc 顺序串行

## 测试 (5 个 unittest, 0 依赖)

```bash
cd task-queue
python -X utf8 -m unittest test_task_queue -v
# 5/5 pass: add basic / priority ordering / run success / failure with retry / failure no retry
```

## License

MIT © fast118


## 💬 联系

扫码加入微信交流群：

![微信群](assets/wechat-qr.png)

或提 [GitHub Issue](https://github.com/fast1188/task-queue/issues)
