import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request


if getattr(sys, "frozen", False):
    # PyInstaller 场景：
    # - RESOURCE_DIR: 打包内只读资源目录（_MEIPASS）
    # - APP_DIR: 用户工作目录（可写，建议与 exe 同目录启动）
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS"))
    APP_DIR = Path.cwd()
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    APP_DIR = RESOURCE_DIR

PROMPT_PATH = APP_DIR / "prompt.txt"
THESIS_PATH = APP_DIR / "论文.txt"
CONFIG_PATH = APP_DIR / "default.yaml"
PROMPTS_DIR = APP_DIR / "prompts"
EXPORT_DIR = APP_DIR / "outputs"
EXPORT_DIR.mkdir(exist_ok=True)
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logger(name: str, file_path: Path) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        filename=str(file_path),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


server_logger = setup_logger("rewrite_server", LOG_DIR / "server.log")
frontend_logger = setup_logger("rewrite_frontend", LOG_DIR / "frontend.log")


@dataclass
class TaskItem:
    task_id: str
    parent_paragraph_id: int
    order: int
    original_text: str
    section_title: str


@dataclass
class RewriteJob:
    job_id: str
    created_at: float
    total: int
    event_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    event_cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    tasks: list[TaskItem] = field(default_factory=list)
    done_count: int = 0
    error_count: int = 0
    results: dict[int, str] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    started: bool = False
    finished: bool = False
    output_file: str | None = None


class StartRequest(BaseModel):
    max_concurrency: int = Field(default=10, ge=1, le=10)


class FrontendLogRequest(BaseModel):
    level: str = Field(default="info")
    message: str
    job_id: str | None = None
    payload: dict[str, Any] | None = None


class DiagnosticsResponse(BaseModel):
    prompt_exists: bool
    thesis_exists: bool
    config_exists: bool
    tasks_total: int
    tasks_over_250_after_split: int
    sample_task_lengths: list[int]
    provider: str
    api_base: str
    model: str
    max_retries: int


app = FastAPI(title="论文分段改写实时对比")
app.mount("/static", StaticFiles(directory=str(RESOURCE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(RESOURCE_DIR / "templates"))

jobs: dict[str, RewriteJob] = {}


def load_llm_config(require_api_key: bool = True) -> dict[str, Any]:
    config_example = RESOURCE_DIR / "default.yaml.example"
    if not CONFIG_PATH.exists() and config_example.exists():
        shutil.copyfile(config_example, CONFIG_PATH)
        server_logger.info("config_auto_init | source=%s | target=%s", str(config_example), str(CONFIG_PATH))

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cfg = data["models"]["default_chat_model"]
    provider = str(cfg.get("model_provider", "openai_compatible")).strip().lower()
    if provider == "openai":
        provider = "openai_compatible"
    env_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY")
    api_key = str(env_api_key or cfg.get("api_key") or "").strip()
    if require_api_key and not api_key:
        raise HTTPException(
            status_code=400,
            detail="未配置 API Key。请在环境变量 OPENAI_API_KEY / AI_API_KEY 或 default.yaml 中设置。",
        )
    default_base_map = {
        "openai_compatible": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
    }
    api_base = str(cfg.get("api_base") or default_base_map.get(provider, "")).rstrip("/")
    if provider not in default_base_map:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 model_provider: {provider}。可选: openai_compatible / anthropic / gemini",
        )

    return {
        "provider": provider,
        "api_base": api_base,
        "api_key": api_key,
        "model": str(cfg["model"]),
        "max_retries": int(cfg.get("max_retries", 3)),
    }


def load_prompt() -> str:
    # 默认优先使用推荐提示词；若 prompt.txt 不存在或为空，自动复制一份
    default_prompt_file = PROMPTS_DIR / "论文修改助手.txt"
    bundled_prompt_file = RESOURCE_DIR / "prompts" / "论文修改助手.txt"
    if not default_prompt_file.exists() and bundled_prompt_file.exists():
        PROMPTS_DIR.mkdir(exist_ok=True)
        shutil.copyfile(bundled_prompt_file, default_prompt_file)
        server_logger.info("prompt_repo_auto_init | source=%s | target=%s", str(bundled_prompt_file), str(default_prompt_file))
    if (not PROMPT_PATH.exists() or not PROMPT_PATH.read_text(encoding="utf-8").strip()) and default_prompt_file.exists():
        shutil.copyfile(default_prompt_file, PROMPT_PATH)
        server_logger.info("prompt_auto_init | source=%s | target=%s", str(default_prompt_file), str(PROMPT_PATH))
    return PROMPT_PATH.read_text(encoding="utf-8")


def extract_main_body(full_text: str) -> str:
    start_match = re.search(r"^\s*1\s*绪\s*论\s*$", full_text, flags=re.MULTILINE)
    if not start_match:
        return full_text.strip()
    start = start_match.start()

    end_match = re.search(r"^\s*参考文献\s*$", full_text, flags=re.MULTILINE)
    end = end_match.start() if end_match and end_match.start() > start else len(full_text)
    return full_text[start:end].strip()


def is_heading_line(text: str) -> bool:
    # 匹配如“1 绪论”“1.1 研究背景”“3.2.1系统总体架构概述”
    return bool(re.match(r"^\d+(?:\.\d+)*\s*[\u4e00-\u9fa5A-Za-z].*", text))


def is_pure_heading_short(text: str, max_len: int = 40) -> bool:
    # 纯标题短段：常见章节标题，且长度较短，不包含句末标点
    if not is_heading_line(text):
        return False
    if len(text.strip()) > max_len:
        return False
    return not any(p in text for p in "。！？；")


def choose_split_index(text: str, max_len: int = 250) -> int:
    if len(text) <= max_len:
        return len(text)
    punct = "。！？；，、：,.!?;:"
    # 优先在 max_len 附近找标点切，尽量让左侧接近上限
    left = max(1, max_len - 80)
    right = min(len(text) - 1, max_len + 80)
    best_idx = -1
    best_dist = 10**9
    target = max_len
    for i in range(left, right):
        if text[i] in punct:
            dist = abs(i - target)
            if dist < best_dist:
                best_dist = dist
                best_idx = i + 1
    if best_idx != -1:
        return best_idx
    return max_len


def split_text_to_max_len(text: str, max_len: int = 250) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_len:
        return [cleaned]
    split_idx = choose_split_index(cleaned, max_len=max_len)
    left = cleaned[:split_idx].strip()
    right = cleaned[split_idx:].strip()
    if not left or not right:
        split_idx = min(max_len, len(cleaned) - 1)
        left = cleaned[:split_idx].strip()
        right = cleaned[split_idx:].strip()
    # 递归切分，确保最终片段都 <= max_len
    return split_text_to_max_len(left, max_len=max_len) + split_text_to_max_len(right, max_len=max_len)


def merge_short_chunks(
    chunks: list[tuple[str, str, int]],
    short_len: int = 15,
    max_len: int = 250,
) -> list[tuple[str, str, int]]:
    """
    合并超短分段，优先并入下一段；若不可行则尝试并入上一段。
    chunks 元素: (section_title, text, parent_paragraph_id)
    """
    items = [[sec, txt, pid] for sec, txt, pid in chunks if txt.strip()]
    i = 0
    while i < len(items):
        sec, txt, pid = items[i]
        cur_len = len(txt)
        if cur_len > short_len:
            i += 1
            continue

        merged = False
        # 优先并入下一段
        if i + 1 < len(items):
            nsec, ntxt, npid = items[i + 1]
            if cur_len + 1 + len(ntxt) <= max_len:
                items[i + 1] = [nsec, f"{txt}\n{ntxt}", npid]
                items.pop(i)
                merged = True

        # 次选并入上一段
        if not merged and i - 1 >= 0:
            psec, ptxt, ppid = items[i - 1]
            if len(ptxt) + 1 + cur_len <= max_len:
                items[i - 1] = [psec, f"{ptxt}\n{txt}", ppid]
                items.pop(i)
                i -= 1
                merged = True

        if not merged:
            i += 1

    return [(sec, txt, pid) for sec, txt, pid in items]


def build_tasks_from_thesis() -> list[TaskItem]:
    full_text = THESIS_PATH.read_text(encoding="utf-8")
    main_body = extract_main_body(full_text)
    lines = [line.strip() for line in main_body.splitlines() if line.strip()]

    tasks: list[TaskItem] = []
    order = 0
    paragraph_id = 0
    current_section = "未分类"
    split_count = 0
    merged_heading_count = 0
    merged_lines: list[tuple[str, str]] = []  # (section_title, text)

    i = 0
    while i < len(lines):
        line = lines[i]
        if is_heading_line(line):
            current_section = line

        # 纯标题短段 + 下一段正文 自动合并
        if (
            is_pure_heading_short(line)
            and i + 1 < len(lines)
            and not is_heading_line(lines[i + 1])
        ):
            merged_lines.append((current_section, f"{line}\n{lines[i + 1]}"))
            merged_heading_count += 1
            i += 2
            continue

        merged_lines.append((current_section, line))
        i += 1

    chunk_rows: list[tuple[str, str, int]] = []
    for section_title, text in merged_lines:
        pieces = split_text_to_max_len(text, max_len=250)
        if len(pieces) > 1:
            split_count += 1
        for piece in pieces:
            chunk_rows.append((section_title, piece, paragraph_id))
        paragraph_id += 1

    merged_chunks = merge_short_chunks(chunk_rows, short_len=15, max_len=250)
    for section_title, piece, pid in merged_chunks:
        tasks.append(
            TaskItem(
                task_id=f"task-{order}",
                parent_paragraph_id=pid,
                order=order,
                original_text=piece,
                section_title=section_title,
            )
        )
        order += 1

    short_after_merge = sum(1 for _, text, _ in merged_chunks if len(text) <= 15)
    server_logger.info(
        "build_tasks | lines=%s | merged_heading=%s | logical_paragraphs=%s | split_over_250=%s | tasks_before_short_merge=%s | tasks=%s | short_after_merge=%s | max_piece_len=%s",
        len(lines),
        merged_heading_count,
        len(merged_lines),
        split_count,
        len(chunk_rows),
        len(tasks),
        short_after_merge,
        max((len(t.original_text) for t in tasks), default=0),
    )
    return tasks


async def push_event(job: RewriteJob, event_type: str, payload: dict[str, Any]) -> None:
    server_logger.info(
        "push_event | job_id=%s | event=%s | queue_size_before=%s",
        job.job_id,
        event_type,
        job.event_queue.qsize(),
    )
    event_obj = {"type": event_type, "data": payload}
    job.event_log.append(event_obj)
    async with job.event_cond:
        job.event_cond.notify_all()
    await job.event_queue.put({"type": event_type, "data": payload})


async def rewrite_one_task(
    job: RewriteJob,
    task: TaskItem,
    prompt_template: str,
    llm_cfg: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> None:
    server_logger.info(
        "task_begin | job_id=%s | task_id=%s | order=%s | parent_paragraph_id=%s | text_len=%s",
        job.job_id,
        task.task_id,
        task.order,
        task.parent_paragraph_id,
        len(task.original_text),
    )
    await push_event(
        job,
        "task_started",
        {
            "job_id": job.job_id,
            "task_id": task.task_id,
            "parent_paragraph_id": task.parent_paragraph_id,
            "order": task.order,
            "section_title": task.section_title,
            "original_text": task.original_text,
        },
    )

    max_retries = max(0, int(llm_cfg["max_retries"]))
    content = prompt_template.replace("{text}", task.original_text)

    async with semaphore:
        for attempt in range(max_retries + 1):
            try:
                server_logger.info(
                    "llm_request | job_id=%s | task_id=%s | order=%s | attempt=%s | provider=%s | model=%s",
                    job.job_id,
                    task.task_id,
                    task.order,
                    attempt + 1,
                    llm_cfg["provider"],
                    llm_cfg["model"],
                )
                async with httpx.AsyncClient(timeout=120.0) as client:
                    rewritten = await request_llm_rewrite(client, content, llm_cfg)

                job.results[task.order] = rewritten
                job.done_count += 1
                server_logger.info(
                    "llm_success | job_id=%s | task_id=%s | order=%s | rewritten_len=%s | done=%s | error=%s",
                    job.job_id,
                    task.task_id,
                    task.order,
                    len(rewritten),
                    job.done_count,
                    job.error_count,
                )
                await push_event(
                    job,
                    "task_done",
                    {
                        "job_id": job.job_id,
                        "task_id": task.task_id,
                        "order": task.order,
                        "section_title": task.section_title,
                        "original_text": task.original_text,
                        "rewritten_text": rewritten,
                        "done_count": job.done_count,
                        "error_count": job.error_count,
                        "total": job.total,
                    },
                )
                return
            except Exception as exc:  # noqa: BLE001
                server_logger.exception(
                    "llm_error | job_id=%s | task_id=%s | order=%s | attempt=%s | error=%s",
                    job.job_id,
                    task.task_id,
                    task.order,
                    attempt + 1,
                    str(exc),
                )
                if attempt >= max_retries:
                    job.error_count += 1
                    job.errors[task.order] = str(exc)
                    await push_event(
                        job,
                        "task_error",
                        {
                            "job_id": job.job_id,
                            "task_id": task.task_id,
                            "order": task.order,
                            "section_title": task.section_title,
                            "original_text": task.original_text,
                            "error": str(exc),
                            "done_count": job.done_count,
                            "error_count": job.error_count,
                            "total": job.total,
                        },
                    )
                    return
                await asyncio.sleep(min(2**attempt, 10))


async def request_llm_rewrite(
    client: httpx.AsyncClient,
    content: str,
    llm_cfg: dict[str, Any],
) -> str:
    provider = llm_cfg["provider"]

    if provider == "openai_compatible":
        resp = await client.post(
            f'{llm_cfg["api_base"]}/chat/completions',
            headers={
                "Authorization": f'Bearer {llm_cfg["api_key"]}',
                "Content-Type": "application/json",
            },
            json={
                "model": llm_cfg["model"],
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    if provider == "anthropic":
        resp = await client.post(
            f'{llm_cfg["api_base"]}/messages',
            headers={
                "x-api-key": llm_cfg["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_cfg["model"],
                "max_tokens": 2048,
                "temperature": 0.7,
                "messages": [{"role": "user", "content": content}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text_parts = [p.get("text", "") for p in data.get("content", []) if p.get("type") == "text"]
        return "".join(text_parts).strip()

    if provider == "gemini":
        resp = await client.post(
            f'{llm_cfg["api_base"]}/models/{llm_cfg["model"]}:generateContent?key={llm_cfg["api_key"]}',
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": content}]}],
                "generationConfig": {"temperature": 0.7},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini 返回为空 candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        return text.strip()

    raise RuntimeError(f"Unsupported provider: {provider}")


def merge_job_output(job: RewriteJob) -> str:
    merged: list[str] = []
    for order in range(job.total):
        merged.append(job.results.get(order, f"[任务失败]{job.errors.get(order, '')}"))
    return "\n\n".join(merged)


def save_job_output(job: RewriteJob) -> str:
    merged = merge_job_output(job)
    filename = f"改写结果_{job.job_id}.txt"
    path = EXPORT_DIR / filename
    path.write_text(merged, encoding="utf-8")
    job.output_file = str(path)
    server_logger.info(
        "save_output | job_id=%s | output_file=%s | done=%s | error=%s",
        job.job_id,
        job.output_file,
        job.done_count,
        job.error_count,
    )
    return str(path)


async def run_job(job: RewriteJob, max_concurrency: int) -> None:
    job.started = True
    prompt_template = load_prompt()
    llm_cfg = load_llm_config()
    semaphore = asyncio.Semaphore(min(10, max_concurrency))
    started_at = time.time()
    server_logger.info(
        "job_start | job_id=%s | total=%s | max_concurrency=%s | prompt_len=%s | api_base=%s | model=%s | max_retries=%s",
        job.job_id,
        job.total,
        min(10, max_concurrency),
        len(prompt_template),
        llm_cfg["api_base"],
        llm_cfg["model"],
        llm_cfg["max_retries"],
    )

    await push_event(
        job,
        "job_started",
        {
            "job_id": job.job_id,
            "total": job.total,
            "max_concurrency": min(10, max_concurrency),
        },
    )

    workers = [
        asyncio.create_task(rewrite_one_task(job, t, prompt_template, llm_cfg, semaphore))
        for t in job.tasks
    ]
    await asyncio.gather(*workers)

    output_path = save_job_output(job)
    job.finished = True
    server_logger.info(
        "job_finish | job_id=%s | elapsed_sec=%.2f | done=%s | error=%s | output_file=%s",
        job.job_id,
        time.time() - started_at,
        job.done_count,
        job.error_count,
        output_path,
    )
    await push_event(
        job,
        "all_done",
        {
            "job_id": job.job_id,
            "total": job.total,
            "done_count": job.done_count,
            "error_count": job.error_count,
            "output_file": output_path,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    server_logger.info("http_get_index")
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/start")
async def start_rewrite(req: StartRequest) -> dict[str, Any]:
    server_logger.info("api_start_request | max_concurrency=%s", req.max_concurrency)
    tasks = build_tasks_from_thesis()
    if not tasks:
        raise HTTPException(status_code=400, detail="未找到可改写正文内容。")

    job_id = uuid.uuid4().hex[:12]
    job = RewriteJob(job_id=job_id, created_at=time.time(), total=len(tasks), tasks=tasks)
    jobs[job_id] = job
    server_logger.info("api_start_created | job_id=%s | total=%s", job_id, len(tasks))
    asyncio.create_task(run_job(job, req.max_concurrency))
    return {"job_id": job_id, "total": len(tasks)}


@app.get("/api/stream/{job_id}")
async def stream_job(job_id: str) -> StreamingResponse:
    server_logger.info("api_stream_connect | job_id=%s", job_id)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")

    async def event_generator() -> Any:
        cursor = 0
        while True:
            while cursor >= len(job.event_log):
                async with job.event_cond:
                    await job.event_cond.wait()
            event = job.event_log[cursor]
            cursor += 1
            ev_type = event["type"]
            data = json.dumps(event["data"], ensure_ascii=False)
            server_logger.info(
                "api_stream_emit | job_id=%s | event=%s | queue_size_after=%s",
                job_id,
                ev_type,
                job.event_queue.qsize(),
            )
            yield f"event: {ev_type}\ndata: {data}\n\n"
            if ev_type == "all_done":
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/result/{job_id}")
async def get_result(job_id: str) -> dict[str, Any]:
    server_logger.info("api_result_request | job_id=%s", job_id)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return {
        "job_id": job.job_id,
        "total": job.total,
        "done_count": job.done_count,
        "error_count": job.error_count,
        "finished": job.finished,
        "merged_text": merge_job_output(job) if job.finished else "",
        "output_file": job.output_file,
    }


@app.get("/api/download/{job_id}")
async def download_result(job_id: str) -> FileResponse:
    server_logger.info("api_download_request | job_id=%s", job_id)
    job = jobs.get(job_id)
    if not job or not job.output_file:
        raise HTTPException(status_code=404, detail="结果文件不存在。")
    return FileResponse(
        path=job.output_file,
        media_type="text/plain",
        filename=Path(job.output_file).name,
    )


@app.post("/api/frontend-log")
async def frontend_log(req: FrontendLogRequest) -> dict[str, str]:
    level = (req.level or "info").lower()
    payload_text = json.dumps(req.payload or {}, ensure_ascii=False)
    msg = f"job_id={req.job_id} | message={req.message} | payload={payload_text}"
    if level == "error":
        frontend_logger.error(msg)
    elif level == "warning":
        frontend_logger.warning(msg)
    else:
        frontend_logger.info(msg)
    return {"status": "ok"}


@app.get("/api/diagnostics", response_model=DiagnosticsResponse)
async def diagnostics() -> DiagnosticsResponse:
    tasks = build_tasks_from_thesis()
    llm_cfg = load_llm_config(require_api_key=False)
    resp = DiagnosticsResponse(
        prompt_exists=PROMPT_PATH.exists(),
        thesis_exists=THESIS_PATH.exists(),
        config_exists=CONFIG_PATH.exists(),
        tasks_total=len(tasks),
        tasks_over_250_after_split=sum(1 for t in tasks if len(t.original_text) > 250),
        sample_task_lengths=[len(t.original_text) for t in tasks[:10]],
        provider=llm_cfg["provider"],
        api_base=llm_cfg["api_base"],
        model=llm_cfg["model"],
        max_retries=llm_cfg["max_retries"],
    )
    server_logger.info("api_diagnostics | %s", json.dumps(resp.dict(), ensure_ascii=False))
    return resp


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
