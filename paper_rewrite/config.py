"""读取 default.yaml；路由层用 load_llm_config，后台任务用 parse_llm_config（不抛 HTTPException）。"""
from __future__ import annotations

import os
import shutil
from typing import Any

import yaml
from fastapi import HTTPException

from paper_rewrite.paths import CONFIG_PATH, RESOURCE_DIR, server_logger


def parse_llm_config(require_api_key: bool = True) -> dict[str, Any]:
    """解析 LLM 配置；失败时抛出 ValueError（供 run_job 捕获）。"""
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
        raise ValueError(
            "未配置 API Key。请在环境变量 OPENAI_API_KEY / AI_API_KEY 或 default.yaml 中设置。",
        )
    default_base_map = {
        "openai_compatible": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
    }
    api_base = str(cfg.get("api_base") or default_base_map.get(provider, "")).rstrip("/")
    if provider not in default_base_map:
        raise ValueError(f"不支持的 model_provider: {provider}。可选: openai_compatible / anthropic / gemini")

    try:
        temperature = float(cfg.get("temperature", 0.7))
    except (TypeError, ValueError):
        temperature = 0.7
    try:
        request_timeout_sec = float(cfg.get("request_timeout_sec", 120))
    except (TypeError, ValueError):
        request_timeout_sec = 120.0
    request_timeout_sec = max(5.0, min(request_timeout_sec, 600.0))

    max_tokens = cfg.get("max_tokens")
    if max_tokens is not None:
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = None

    anthropic_max_tokens = cfg.get("anthropic_max_tokens", 2048)
    try:
        anthropic_max_tokens = int(anthropic_max_tokens)
    except (TypeError, ValueError):
        anthropic_max_tokens = 2048

    return {
        "provider": provider,
        "api_base": api_base,
        "api_key": api_key,
        "model": str(cfg["model"]),
        "max_retries": int(cfg.get("max_retries", 3)),
        "temperature": temperature,
        "request_timeout_sec": request_timeout_sec,
        "max_tokens": max_tokens,
        "anthropic_max_tokens": anthropic_max_tokens,
    }


def load_llm_config(require_api_key: bool = True) -> dict[str, Any]:
    """供 FastAPI 路由使用：ValueError 转为 HTTP 400。"""
    try:
        return parse_llm_config(require_api_key=require_api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
