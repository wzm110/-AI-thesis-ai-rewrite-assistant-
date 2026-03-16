# 维护者发布说明（Maintainer Only）

> 本文档面向项目维护者，不面向普通用户。

## 1. 发布前体检

双击运行：

`pre_release_check.bat`

该脚本会自动：

- 检查 `server.py` 语法
- 检查分段逻辑是否满足 `<=250`
- 检测 `default.yaml` 是否疑似包含真实 API Key
- 清理 `logs/` 和 `outputs/`

## 2. 打包 EXE

双击运行：

`build_release.bat`

打包完成后产物在：

`dist/release/`

可将该目录整体交付给最终用户（用户双击 `run_exe.bat` 即可使用）。

## 3. 仓库安全检查

确认以下文件未提交真实数据：

- `default.yaml`
- `prompt.txt`
- `论文.txt`
- `logs/*`
- `outputs/*`

建议仅提交：

- `default.yaml.example`
- `README.md`
- 源码、脚本、模板、静态资源

## 4. GitHub 首发流程（示例）

```bash
git init
git add .
git commit -m "init: open source thesis rewrite assistant"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```
