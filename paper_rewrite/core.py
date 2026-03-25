"""分段任务构建、并发改写、内存任务表与事件。"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from paper_rewrite.config import parse_llm_config
from paper_rewrite.llm import request_llm_rewrite
from paper_rewrite.paths import EXPORT_DIR, THESIS_PATH, server_logger
from paper_rewrite.prompt import load_prompt

MAX_JOBS_RETAINED = 20

jobs: dict[str, "RewriteJob"] = {}


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
    # SSE 仅消费 event_log；与 asyncio.Condition 配合推送新事件
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


def register_job(job_id: str, job: RewriteJob) -> None:
    jobs[job_id] = job
    while len(jobs) > MAX_JOBS_RETAINED:
        oldest = min(jobs.keys(), key=lambda k: jobs[k].created_at)
        del jobs[oldest]
        server_logger.info("jobs_prune | removed_job_id=%s | remaining=%s", oldest, len(jobs))


def extract_main_body(full_text: str) -> str:
    start_match = re.search(r"^\s*1\s*绪\s*论\s*$", full_text, flags=re.MULTILINE)
    if not start_match:
        return full_text.strip()
    start = start_match.start()

    end_match = re.search(r"^\s*参考文献\s*$", full_text, flags=re.MULTILINE)
    end = end_match.start() if end_match and end_match.start() > start else len(full_text)
    return full_text[start:end].strip()


def is_heading_line(text: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+)*\s*[\u4e00-\u9fa5A-Za-z].*", text))


def is_pure_heading_short(text: str, max_len: int = 40) -> bool:
    if not is_heading_line(text):
        return False
    if len(text.strip()) > max_len:
        return False
    return not any(p in text for p in "。！？；")


def choose_split_index(text: str, max_len: int = 250) -> int:
    if len(text) <= max_len:
        return len(text)
    punct = "。！？；，、：,.!?;:"
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
    return split_text_to_max_len(left, max_len=max_len) + split_text_to_max_len(right, max_len=max_len)


def merge_short_chunks(
    chunks: list[tuple[str, str, int]],
    short_len: int = 15,
    max_len: int = 250,
) -> list[tuple[str, str, int]]:
    items = [[sec, txt, pid] for sec, txt, pid in chunks if txt.strip()]
    i = 0
    while i < len(items):
        sec, txt, pid = items[i]
        cur_len = len(txt)
        if cur_len > short_len:
            i += 1
            continue

        merged = False
        if i + 1 < len(items):
            nsec, ntxt, npid = items[i + 1]
            if cur_len + 1 + len(ntxt) <= max_len:
                items[i + 1] = [nsec, f"{txt}\n{ntxt}", npid]
                items.pop(i)
                merged = True

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
    merged_lines: list[tuple[str, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if is_heading_line(line):
            current_section = line

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
        "push_event | job_id=%s | event=%s",
        job.job_id,
        event_type,
    )
    event_obj = {"type": event_type, "data": payload}
    job.event_log.append(event_obj)
    async with job.event_cond:
        job.event_cond.notify_all()


async def rewrite_one_task(
    job: RewriteJob,
    task: TaskItem,
    prompt_template: str,
    llm_cfg: dict[str, Any],
    semaphore: asyncio.Semaphore,
    http_client: httpx.AsyncClient,
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
                rewritten = await request_llm_rewrite(http_client, content, llm_cfg)

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


async def run_job(job: RewriteJob, max_concurrency: int, http_client: httpx.AsyncClient) -> None:
    job.started = True
    try:
        prompt_template = load_prompt()
        llm_cfg = parse_llm_config(require_api_key=True)
    except ValueError as e:
        server_logger.error("run_job_config_failed | job_id=%s | err=%s", job.job_id, e)
        job.finished = True
        job.error_count = job.total
        for i in range(job.total):
            job.errors[i] = str(e)
        await push_event(
            job,
            "all_done",
            {
                "job_id": job.job_id,
                "total": job.total,
                "done_count": 0,
                "error_count": job.total,
                "output_file": None,
            },
        )
        return
    except OSError as e:
        server_logger.exception("run_job_prompt_failed | job_id=%s", job.job_id)
        job.finished = True
        job.error_count = job.total
        for i in range(job.total):
            job.errors[i] = f"读取提示词失败: {e}"
        await push_event(
            job,
            "all_done",
            {
                "job_id": job.job_id,
                "total": job.total,
                "done_count": 0,
                "error_count": job.total,
                "output_file": None,
            },
        )
        return

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
        asyncio.create_task(
            rewrite_one_task(job, t, prompt_template, llm_cfg, semaphore, http_client),
        )
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
