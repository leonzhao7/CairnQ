# LLM 自动创建项目功能

## 概述

Server 现在支持在启动时通过 LLM 自动创建 Cairn 项目。你只需提供任务描述，LLM 会自动提取项目的起点（origin）、目标（goal）和提示（hints）。

## 配置方法

### 1. 创建 LLM 配置文件

复制示例配置并编辑：

```bash
cp llm.example.json llm.json
```

编辑 `llm.json`，填入你的 API 配置：

```json
{
  "url": "https://api.openai.com/v1",
  "api_key": "YOUR_API_KEY_HERE",
  "model": "gpt-4o",
  "endpoint": "/chat/completions",
  "max_tokens": 65536,
  "temperature": 0.2,
  "timeout": 600,
  "retries": 2
}
```

支持的配置参数：
- `url`: API 的基础 URL
- `api_key`: 你的 API 密钥
- `model`: 模型名称（如 `gpt-4`, `claude-sonnet-4`）
- `endpoint`: API 路径，必须是 `/chat/completions`（OpenAI 兼容格式）
- `max_tokens`: 响应的最大 token 数（默认：65536）
- `temperature`: 采样温度（默认：0.2）
- `timeout`: 请求超时时间，单位秒（默认：600）
- `retries`: 临时失败的重试次数（默认：2）
- `proxy`: 可选的 HTTP/HTTPS 代理 URL
- `extra_headers`: 可选的额外 HTTP 头，JSON 对象格式

### 2. 创建任务描述文件

复制示例文件并编辑：

```bash
cp start_prompt.example.md start_prompt.md
```

编辑 `start_prompt.md`，写入你的实际任务描述：

```markdown
目标：192.168.1.100
任务：获取 root 权限并读取 /root/flag.txt

已知信息：
- 目标运行 Web 服务器，端口 80
- SSH 在 22 端口可用
- 系统看起来是 Ubuntu 20.04

限制条件：
- 不使用暴力破解
- 只针对指定 IP 地址
```

### 3. 启动 Server

使用 `start.sh` 或直接运行：

```bash
uv run --project cairn cairn serve
```

如果两个文件都存在，server 会：
1. 读取 `start_prompt.md` 中的任务描述
2. 发送给 LLM 进行结构化提取
3. 创建一个 Cairn 项目
4. 输出：`Created startup project: proj_001`
5. 正常启动并监听

### 4. 自定义配置文件路径

你可以通过命令行参数指定不同的配置文件：

```bash
uv run --project cairn cairn serve \
  --llm-config my_llm.json \
  --start-prompt my_task.md
```

## 特性说明

### 完全可选
- 如果不存在 `llm.json` 或 `start_prompt.md`，server 会跳过项目创建，正常启动
- LLM 调用失败不会阻止 server 启动
- 这是一个纯增强功能，不影响现有工作流

### 自动跳过场景
- LLM 配置文件不存在
- 任务描述文件不存在
- 任务描述文件为空
- LLM 配置文件格式错误
- LLM 调用失败
- LLM 返回的 JSON 格式错误

所有跳过情况都会记录日志，server 继续正常启动。

### LLM 提取的信息

LLM 会将你的任务描述转换为：

- `title`: 项目标题（简短描述）
- `origin`: 起点事实（目标信息、已知凭证、环境等）
- `goal`: 终点条件（成功的明确标准）
- `hints`: 提示数组（给 agent 的线索和建议）
- `bootstrap_enabled`: 是否启用 bootstrap 模式（默认 true）

### 示例转换

**输入 (`start_prompt.md`):**
```
渗透测试目标：10.0.0.5
目标：获取 root shell 并读取 flag

已知：
- Web 服务在 80 端口
- 可能存在 SQL 注入漏洞
```

**LLM 输出（自动创建的项目）:**
```json
{
  "title": "渗透测试 10.0.0.5",
  "origin": "目标：10.0.0.5，Web 服务运行在 80 端口",
  "goal": "获取 10.0.0.5 的 root shell 并读取 flag",
  "hints": [
    {"content": "从端口扫描开始", "creator": "llm"},
    {"content": "检查 Web 应用的 SQL 注入漏洞", "creator": "llm"}
  ],
  "bootstrap_enabled": true
}
```

## 文件位置

默认文件位置：
- LLM 配置：`./llm.json`（已添加到 .gitignore）
- 任务描述：`./start_prompt.md`（已添加到 .gitignore）
- 示例文件：`llm.example.json` 和 `start_prompt.example.md`

## 测试

运行测试验证功能：

```bash
uv run --project cairn --group dev pytest cairn/tests/test_startup_project.py -v
```

所有测试都应该通过。
