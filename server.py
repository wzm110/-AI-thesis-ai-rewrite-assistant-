import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from paper_rewrite.config import load_llm_config
from paper_rewrite.core import (
    RewriteJob,
    build_tasks_from_thesis,
    jobs,
    merge_job_output,
    register_job,
    run_job,
)
from paper_rewrite.docx_extract import MAX_DOCX_BYTES, extract_plain_text_from_docx
from paper_rewrite.paths import (
    CONFIG_PATH,
    PROMPT_PATH,
    RESOURCE_DIR,
    THESIS_PATH,
    frontend_logger,
    server_logger,
)
from paper_rewrite.prompt import load_prompt


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


MAX_FRONTEND_LOG_CHARS = 8000


@asynccontextmanager
async def lifespan(app: FastAPI):
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        app.state.http_client = client
        yield


app = FastAPI(title="论文分段改写实时对比", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(RESOURCE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(RESOURCE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    server_logger.info("http_get_index")
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/api/start")
async def start_rewrite(req: StartRequest, request: Request) -> dict[str, Any]:
    server_logger.info("api_start_request | max_concurrency=%s", req.max_concurrency)
    load_llm_config(require_api_key=True)
    try:
        load_prompt()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取提示词失败：{e}") from e

    tasks = build_tasks_from_thesis()
    if not tasks:
        raise HTTPException(status_code=400, detail="未找到可改写正文内容。")

    job_id = uuid.uuid4().hex[:12]
    job = RewriteJob(job_id=job_id, created_at=time.time(), total=len(tasks), tasks=tasks)
    register_job(job_id, job)
    server_logger.info("api_start_created | job_id=%s | total=%s", job_id, len(tasks))

    http_client: httpx.AsyncClient = request.app.state.http_client
    asyncio.create_task(run_job(job, req.max_concurrency, http_client))
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
            server_logger.debug(
                "api_stream_emit | job_id=%s | event=%s",
                job_id,
                ev_type,
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
    if len(req.message) > MAX_FRONTEND_LOG_CHARS:
        raise HTTPException(status_code=400, detail="message 过长。")
    payload_text = json.dumps(req.payload or {}, ensure_ascii=False)
    if len(payload_text) > MAX_FRONTEND_LOG_CHARS:
        raise HTTPException(status_code=400, detail="payload 过大。")
    level = (req.level or "info").lower()
    msg = f"job_id={req.job_id} | message={req.message} | payload={payload_text}"
    if level == "error":
        frontend_logger.error(msg)
    elif level == "warning":
        frontend_logger.warning(msg)
    else:
        frontend_logger.info(msg)
    return {"status": "ok"}


@app.post("/api/upload-docx")
async def upload_docx(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传 .docx，提取正文纯文本（忽略图片），覆盖写入 论文.txt。"""
    name = (file.filename or "").lower()
    if not name.endswith(".docx"):
        raise HTTPException(status_code=400, detail="请上传 .docx 格式的 Word 文件。")
    data = await file.read()
    if len(data) > MAX_DOCX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大，请限制在 {MAX_DOCX_BYTES // (1024 * 1024)}MB 以内。",
        )
    if len(data) < 4:
        raise HTTPException(status_code=400, detail="文件无效或为空。")
    if data[:2] != b"PK":
        raise HTTPException(status_code=400, detail="不是有效的 .docx 文件。")
    try:
        text = extract_plain_text_from_docx(data)
    except Exception as e:
        server_logger.exception("upload_docx_parse_failed")
        raise HTTPException(status_code=400, detail=f"解析 Word 失败：{e}") from e
    if not text.strip():
        raise HTTPException(status_code=400, detail="未解析到文本（可能仅含图片或空白内容）。")
    try:
        THESIS_PATH.write_text(text, encoding="utf-8")
    except OSError as e:
        server_logger.exception("upload_docx_write_failed")
        raise HTTPException(status_code=500, detail=f"写入论文.txt 失败：{e}") from e
    server_logger.info(
        "upload_docx_ok | bytes=%s | chars=%s | path=%s",
        len(data),
        len(text),
        str(THESIS_PATH),
    )
    return {"ok": True, "path": str(THESIS_PATH), "chars": len(text)}


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
    server_logger.info("api_diagnostics | %s", json.dumps(resp.model_dump(), ensure_ascii=False))
    return resp


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
