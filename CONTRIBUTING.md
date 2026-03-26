# 贡献指南

欢迎提交问题与改进建议。如果你愿意让项目更好，也可以直接提交 PR。

## 开发环境

1. 安装依赖（开发/测试）：
   - `pip install -e .[dev]`
2. 运行单测：
   - `pytest`
3. 本地冒烟（可选）：
   - `python tests/qa_smoke.py`

## LLM 端到端联调（可选，不建议在 CI 默认运行）

如果你希望验证某个 LLM provider 的连通性与端到端改写流程，可手动运行：

- `python tests/integration_llm_env.py`

运行前请先设置环境变量：`OPENAI_API_KEY` 或 `AI_API_KEY`。

## 提交 PR 的建议

- 尽量为关键逻辑补充“不依赖 LLM”的单测（例如切分/合并规则）。
- 文档更新尽量包含：新增/变更的用途、参数、以及如何复现问题。

