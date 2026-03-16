# 论文降AI率助手（开源版）

> 面向毕业论文场景的本地改写工具：分段调用 LLM、实时返回、可视化对比、低门槛使用。

---

## 背景

想免费降AI的来薅羊毛来，我最近在论文降 AI 率时，发现我一篇2万多字的就要60-180不等。

这个项目的目标是：**想免费降AI率的，就别花那冤枉钱**，让没有代码基础的同学也能用。

## 更新计划：现在需要你把你的论文手动将内容粘贴复制到`论文.txt`这个文件就行，然后你再从浏览器或者`outputs`文件夹里面修改好的内容替换你原本内容就行。
- 后续可能直接把你论文文档上传浏览器自动解析，自动生成修改好的论文docx。

---

## 项目简介

本工具会读取你的 `论文.txt`，按规则切分为小段（每段不超过 250 字），并发调用 LLM 改写，然后在网页中实时展示：

- 原文
- 改写后
- 差异高亮
- 任务进度与导出结果

默认推荐提示词：`prompts/论文修改助手.txt`。  
如果 `prompt.txt` 为空或不存在，程序会自动初始化为这份提示词。

---

## 效果展示

### 降 AI 前

![降AI率前检测报告](images/降AI率前检测报告.png)

### 降 AI 后

![降AI率后检测](images/降AI率后检测.png)

---

## 核心能力

- 分段改写：单段长度自动控制在 `<=250`
- 短段合并：自动合并超短段，减少无效请求
- 并发可控：并发上限 `10`
- 实时流式：SSE 即时返回，不必等待全部完成
- 对比可视：原文 / 改写 / diff 同屏查看
- 结果导出：一键下载完整改写文本

---

## 快速开始（无代码用户）

先把你的论文正文粘贴到 `论文.txt`。

### 方式 A：源码一键启动（推荐）

1. 安装 Python 3.10+
2. 双击 `start.bat`
3. 浏览器会自动打开 `http://127.0.0.1:8000`
4. 点击页面“开始改写”

### 方式 B：EXE 发行版（给普通用户）

开发者打包后会得到 `dist/release/`，用户只需：

1. 双击 `run_exe.bat`
2. 浏览器自动打开页面
3. 按页面提示操作

---

## 使用说明

### 1) 准备文件

- `论文.txt`：待改写正文
- `prompt.txt`：生效提示词
- `default.yaml`：模型配置（支持多种 LLM API）

### 2) 选择 LLM API 提供商（先区分“协议类型”和“厂商”）

当前支持：

- `openai_compatible`：**OpenAI 兼容协议类型**（不是某一家厂商）
  - 可接入：OpenAI、阿里云 DashScope 兼容模式、DeepSeek、Moonshot、SiliconFlow、OpenRouter 等
- `anthropic`：Claude 官方接口
- `gemini`：Google Gemini 官方接口

你只需要修改 `default.yaml` 的 `model_provider`、`api_base`、`model` 即可。

#### A. OpenAI 兼容接口（推荐，覆盖最广）

```yaml
models:
  default_chat_model:
    model_provider: openai_compatible
    api_key: ""
    model: qwen3-max
    api_base: https://dashscope.aliyuncs.com/compatible-mode/v1
```

上面这个 `api_base` 是**阿里云 DashScope 的 OpenAI 兼容地址**。  
如果你切换到其他兼容平台，只需替换 `api_base` 和 `model`，`model_provider` 仍保持 `openai_compatible`。

#### B. Anthropic（Claude）

```yaml
models:
  default_chat_model:
    model_provider: anthropic
    api_key: ""
    model: claude-3-5-sonnet-latest
    api_base: https://api.anthropic.com/v1
```

#### C. Gemini

```yaml
models:
  default_chat_model:
    model_provider: gemini
    api_key: ""
    model: gemini-1.5-pro
    api_base: https://generativelanguage.googleapis.com/v1beta
```

### 3) 配置 API Key（推荐环境变量）

```bash
set OPENAI_API_KEY=你的key
```

也支持写入 `default.yaml` 的 `api_key` 字段（不建议公开仓库存真实 key）。

### 4) 切换提示词

从 `prompts/` 里任选一个，把内容复制到 `prompt.txt` 即可。  
推荐：`prompts/论文修改助手.txt`。

---

## 开发者打包发行

1. 双击 `build_release.bat`
2. 打包完成后产物在 `dist/release/`
3. 把 `dist/release/` 整个目录发给用户即可

## 维护与发布

维护者相关说明（如发布前检查、打包流程、仓库发布）已单独放在：`docs/RELEASE.md`。

---

## 安全与隐私

- `logs/`、`outputs/`、`dist/`、`build/` 已加入 `.gitignore`
- `default.yaml`、`prompt.txt`、`论文.txt` 默认加入 `.gitignore`（避免误传本地数据）
- 建议仅使用环境变量注入 API Key
- 仓库中建议保留 `default.yaml.example` 作为配置模板

---

## 常见问题

- **前端没有实时数据**：查看 `logs/server.log` 是否有 `api_stream_emit`
- **启动失败**：确认 Python 已加入 PATH
- **请求报错**：检查 `model_provider`、`api_base`、`model`、`OPENAI_API_KEY`

---

作者主页：[wzm110](https://github.com/wzm110)
