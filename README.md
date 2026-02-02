# Grok MCP Search Server

一个轻量的 MCP（Model Context Protocol）JSON-RPC 服务，用于将 Grok 搜索能力封装成 MCP 工具。服务端使用 Flask 接收 MCP 请求，并通过子进程调用 CLI 脚本完成实际的 Grok 请求与响应解析。

可配合grok2api項目使用

## 功能概述

- 提供 MCP JSON-RPC 端点，支持 `initialize`、`tools/list`、`tools/call`。
- `tools/call` 会调用 CLI 脚本执行 Grok 搜索，并以 MCP `content` 文本返回。
- 支持从配置文件、环境变量或 CLI 参数读取 Grok API 相关设置。

相关文件：
- 服务入口：[`mcp_server.py`](mcp_server.py:1)
- CLI：[`scripts/grok_search.py`](scripts/grok_search.py:1)
- 容器化：[`Dockerfile`](Dockerfile:1)、[`docker-compose.yml`](docker-compose.yml:1)
- 配置：[`config.json`](config.json:1)

## 部署方式

### Docker Compose

1. 准备配置（建议用环境变量覆盖敏感信息）：
   - 必需：`GROK_BASE_URL`、`GROK_API_KEY`
   - 可选：`GROK_MODEL`、`GROK_TIMEOUT_SECONDS`、`GROK_SYSTEM_PROMPT`

2. 构建并启动：

```bash
docker-compose up -d --build
```

3. 服务默认监听：`http://127.0.0.1:5678/`

> 注意：`docker-compose.yml` 使用 `network_mode: host`，在 Windows 环境下行为可能与 Linux 不一致，必要时可改为端口映射（例如 `ports: ["5678:5678"]`）。


## 

### 注意事项
---
⚠️ 本项目仅供学习和研究目的，请遵守相关使用条款。