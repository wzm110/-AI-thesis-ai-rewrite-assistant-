from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx
import uvicorn

from paper_rewrite.config import parse_llm_config
from paper_rewrite.core import RewriteJob, build_tasks_from_thesis, register_job, run_job, save_job_output
from paper_rewrite.docx_extract import MAX_DOCX_BYTES, extract_plain_text_from_docx
from paper_rewrite.paths import EXPORT_DIR, PROMPT_PATH, CONFIG_PATH, THESIS_PATH
from paper_rewrite.prompt import load_prompt


def _read_text_input(in_path: Path) -> str:
    if in_path.suffix.lower() == ".docx":
        data = in_path.read_bytes()
        if len(data) > MAX_DOCX_BYTES:
            raise ValueError(f"输入 .docx 过大（>{MAX_DOCX_BYTES // (1024 * 1024)}MB）。")
        text = extract_plain_text_from_docx(data)
        if not text.strip():
            raise ValueError("未解析到文本（可能仅含图片或空白内容）。")
        return text
    return in_path.read_text(encoding="utf-8")


async def _rewrite_async(in_path: Path, max_concurrency: int, out_path: Path | None) -> dict[str, object]:
    THESIS_PATH.write_text(_read_text_input(in_path), encoding="utf-8")
    load_prompt()

    tasks = build_tasks_from_thesis()
    if not tasks:
        raise ValueError("未找到可改写正文内容。")

    # 强制校验 API Key（由 default.yaml / 环境变量提供）
    llm_cfg = parse_llm_config(require_api_key=True)
    _ = llm_cfg  # llm_cfg 只用于触发校验与初始化，run_job 内部会再次解析

    job_id = uuid.uuid4().hex[:12]
    job = RewriteJob(job_id=job_id, created_at=time.time(), total=len(tasks), tasks=tasks)
    register_job(job_id, job)

    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        await run_job(job, max_concurrency=max_concurrency, http_client=client)

    if not job.output_file:
        # 理论上 run_job 总会保存输出；兜底以防未来 refactor 改动
        save_job_output(job)

    if out_path and job.output_file:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job.output_file, out_path)

    return {
        "job_id": job_id,
        "total": job.total,
        "done_count": job.done_count,
        "error_count": job.error_count,
        "output_file": job.output_file,
        "out_path": str(out_path) if out_path else None,
    }


def cmd_rewrite(args: argparse.Namespace) -> None:
    in_path = Path(args.in_file).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve() if args.out else None
    max_concurrency = int(args.concurrency)

    if not in_path.exists():
        raise SystemExit(f"输入文件不存在：{in_path}")

    result = asyncio.run(_rewrite_async(in_path=in_path, max_concurrency=max_concurrency, out_path=out_path))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"job_id={result['job_id']} total={result['total']} done={result['done_count']} error={result['error_count']}")
        if result["output_file"]:
            print(f"output={result['output_file']}")


def cmd_extract(args: argparse.Namespace) -> None:
    in_path = Path(args.in_file).expanduser().resolve()
    if not in_path.exists():
        raise SystemExit(f"输入文件不存在：{in_path}")

    text = _read_text_input(in_path)

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(str(out_path))
    else:
        sys.stdout.write(text)


def cmd_doctor(args: argparse.Namespace) -> None:
    thesis_exists = THESIS_PATH.exists()
    prompt_exists = PROMPT_PATH.exists()
    config_exists = CONFIG_PATH.exists()

    tasks_total = 0
    tasks_over_250 = 0
    sample_task_lengths: list[int] = []

    if thesis_exists:
        try:
            tasks = build_tasks_from_thesis()
            tasks_total = len(tasks)
            tasks_over_250 = sum(1 for t in tasks if len(t.original_text) > 250)
            sample_task_lengths = [len(t.original_text) for t in tasks[:10]]
        except Exception:
            # 避免 doctor 直接报栈导致用户无法定位问题
            tasks_total = 0

    llm_cfg = parse_llm_config(require_api_key=False)

    payload = {
        "prompt_exists": prompt_exists,
        "thesis_exists": thesis_exists,
        "config_exists": config_exists,
        "tasks_total": tasks_total,
        "tasks_over_250_after_split": tasks_over_250,
        "sample_task_lengths": sample_task_lengths,
        "provider": llm_cfg["provider"],
        "api_base": llm_cfg["api_base"],
        "model": llm_cfg["model"],
        "max_retries": llm_cfg["max_retries"],
        "outputs_dir": str(EXPORT_DIR),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"thesis_exists={payload['thesis_exists']} prompt_exists={payload['prompt_exists']} config_exists={payload['config_exists']}")
        print(f"tasks_total={payload['tasks_total']} tasks_over_250_after_split={payload['tasks_over_250_after_split']}")
        print(f"model_provider={payload['provider']} model={payload['model']} api_base={payload['api_base']}")


def cmd_serve(args: argparse.Namespace) -> None:
    uvicorn.run(
        "paper_rewrite.web_app:app",
        host=args.host,
        port=int(args.port),
        reload=bool(args.reload),
        log_level=args.log_level,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-rewrite", description="论文/技术文档分段改写工具箱（Web + CLI）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rewrite = sub.add_parser("rewrite", help="读取输入文件并开始改写")
    p_rewrite.add_argument("--in", dest="in_file", required=True, help="输入文件：.txt 或 .docx")
    p_rewrite.add_argument("--out", default=None, help="输出文件（可选；默认写入 outputs/）")
    p_rewrite.add_argument("--concurrency", default=10, type=int, choices=range(1, 11), help="并发上限（1-10）")
    p_rewrite.add_argument("--json", action="store_true", help="输出 JSON 结果")
    p_rewrite.set_defaults(func=cmd_rewrite)

    p_extract = sub.add_parser("extract", help="提取 .docx 或读取 .txt 为纯文本")
    p_extract.add_argument("--in", dest="in_file", required=True, help="输入文件：.txt 或 .docx")
    p_extract.add_argument("--out", default=None, help="输出文本文件（可选；不填则输出到 stdout）")
    p_extract.set_defaults(func=cmd_extract)

    p_doctor = sub.add_parser("doctor", help="显示配置/文件/分段统计")
    p_doctor.add_argument("--json", action="store_true", help="输出 JSON 结果")
    p_doctor.set_defaults(func=cmd_doctor)

    p_serve = sub.add_parser("serve", help="启动本地 Web 服务")
    p_serve.add_argument("--host", default="127.0.0.1", help="监听地址")
    p_serve.add_argument("--port", default=8000, help="监听端口")
    p_serve.add_argument("--reload", action="store_true", help="开发模式自动重载")
    p_serve.add_argument("--log-level", default="info", help="uvicorn log level")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

