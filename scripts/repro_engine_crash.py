"""复现引擎崩溃并打印真实 traceback（父进程捕获子进程 stderr）。

用法（容器内）：docker compose exec -T api python /app/scripts/repro_engine_crash.py <input.json>
"""
import os
import shutil
import subprocess
import sys

inp = sys.argv[1]
out = inp.replace("_input.json", "_output.json")

# 备份原 output，避免覆盖证据
if os.path.exists(out):
    shutil.copy(out, out + ".keep")

env = dict(os.environ)
env["RAT_ACCOUNT_ID"] = "af28b675-e0b8-46ac-96e7-9e4b3e3f485a"

r = subprocess.run(
    [sys.executable, "-m", "server.engine_runner", inp],
    capture_output=True,
    text=True,
    cwd="/app",
    env=env,
    timeout=600,
)
print("returncode =", r.returncode)
print("=" * 30, "STDOUT (tail)", "=" * 30)
print(r.stdout[-4000:])
print("=" * 30, "STDERR (tail)", "=" * 30)
print(r.stderr[-6000:])
