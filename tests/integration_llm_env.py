"""
端到端 LLM 测试：仅当环境变量 OPENAI_API_KEY（或 AI_API_KEY）已设置时运行。
不读取、不写入任何包含密钥的项目文件。

从项目根执行：
  python tests/integration_llm_env.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent

MINIMAL_THESIS = """1 绪论

这是一段连通性测试，仅验证 API 可调用。
"""


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    if not (os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY")):
        print("未设置 OPENAI_API_KEY / AI_API_KEY，跳过。")
        return 0

    from server import THESIS_PATH  # noqa: PLC0415

    backup: bytes | None = THESIS_PATH.read_bytes() if THESIS_PATH.exists() else None
    port = 8877
    proc: subprocess.Popen[bytes] | None = None

    try:
        THESIS_PATH.write_text(MINIMAL_THESIS, encoding="utf-8")

        env = os.environ.copy()
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", f"--port={port}"],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(2.5)
        if proc.poll() is not None:
            err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            print("uvicorn 启动失败:", err[:2000])
            return 1

        base = f"http://127.0.0.1:{port}"
        req = Request(
            f"{base}/api/start",
            data=json.dumps({"max_concurrency": 1}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=60) as r:
                start_data = json.loads(r.read().decode("utf-8"))
        except HTTPError as e:
            print("POST /api/start 失败:", e.code, e.read().decode("utf-8", errors="replace")[:500])
            return 1

        job_id = start_data.get("job_id")
        total = start_data.get("total", 0)
        print(f"job_id={job_id} total_tasks={total}")

        deadline = time.time() + 420
        while time.time() < deadline:
            time.sleep(2)
            try:
                with urlopen(f"{base}/api/result/{job_id}", timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except HTTPError as e:
                print("GET result:", e.code)
                continue

            if data.get("finished"):
                done = data.get("done_count", 0)
                errc = data.get("error_count", 0)
                merged = data.get("merged_text") or ""
                print(f"finished done={done} error_count={errc} merged_len={len(merged)}")
                if errc > 0:
                    print("存在失败分段，请查日志。")
                    return 1
                if done < 1:
                    print("无成功分段")
                    return 1
                print("集成测试通过：LLM 返回了改写结果。")
                return 0

        print("超时未完成")
        return 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        if backup is not None:
            THESIS_PATH.write_bytes(backup)
            print("已恢复 论文.txt")
        elif THESIS_PATH.exists():
            THESIS_PATH.unlink()
            print("已删除测试用 论文.txt（原不存在）")


if __name__ == "__main__":
    raise SystemExit(main())
