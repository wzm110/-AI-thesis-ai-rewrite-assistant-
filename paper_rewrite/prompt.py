"""prompt.txt 初始化与读取。"""
from __future__ import annotations

import shutil

from paper_rewrite.paths import PROMPT_PATH, PROMPTS_DIR, RESOURCE_DIR, server_logger


def load_prompt() -> str:
    default_prompt_file = PROMPTS_DIR / "论文修改助手.txt"
    bundled_prompt_file = RESOURCE_DIR / "prompts" / "论文修改助手.txt"
    if not default_prompt_file.exists() and bundled_prompt_file.exists():
        PROMPTS_DIR.mkdir(exist_ok=True)
        shutil.copyfile(bundled_prompt_file, default_prompt_file)
        server_logger.info(
            "prompt_repo_auto_init | source=%s | target=%s",
            str(bundled_prompt_file),
            str(default_prompt_file),
        )
    if (not PROMPT_PATH.exists() or not PROMPT_PATH.read_text(encoding="utf-8").strip()) and default_prompt_file.exists():
        shutil.copyfile(default_prompt_file, PROMPT_PATH)
        server_logger.info("prompt_auto_init | source=%s | target=%s", str(default_prompt_file), str(PROMPT_PATH))
    return PROMPT_PATH.read_text(encoding="utf-8")
