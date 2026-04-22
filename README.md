# Claw-like AI Assistant Desktop

这是一个可直接运行的 Claw 风格 Agent 原型，包含 FastAPI 后端 + 前端工作台 + 桌面封装入口，覆盖以下能力：

- 多会话管理（创建、搜索、重命名、删除、置顶、归档）
- 请求级上下文引用（跨会话消息引用）
- 请求级 Skill 选择与调用记录
- 对话消息存储与溯源卡片
- 书签标记与查询
- 前端单页工作台（会话侧栏、消息流、上下文引用、Skill 面板、书签面板）
- App 内配置中心（模型 API、Agent 引擎、本地 Skill 目录）
- 桌面封装入口（可持久化本地 Skill 与设置）

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 启动服务

```bash
uvicorn app:app --reload
```

启动后可访问：

- API 文档: <http://127.0.0.1:8000/docs>
- 健康检查: <http://127.0.0.1:8000/health>
- 前端页面: <http://127.0.0.1:8000/>

## 2.1 启动桌面 App

```bash
python desktop_app.py
```

桌面模式会自动拉起本地后端并嵌入 UI，不依赖浏览器标签页。

如需打包可执行文件：

```bash
bash build_desktop.sh
```

## 3. 关键接口

- `GET /skills`
- `GET /cli/status`
- `POST /cli/skillhub/install`
- `POST /cli/exec`（白名单：`openclaw`/`skillhub`）
- `GET /skills/local`
- `POST /skills/local/install`
- `POST /skills/local/upgrade`
- `DELETE /skills/local/{skill_key}`
- `GET /models`
- `GET /settings`
- `PUT /settings`
- `POST /conversations`
- `GET /conversations`
- `PATCH /conversations/{conversation_id}`
- `POST /conversations/{conversation_id}/messages/send`
- `POST /conversations/{conversation_id}/messages/stream` (SSE 流式)
- `GET /conversations/{conversation_id}`
- `GET /context/messages/search`
- `POST /messages/{message_id}/bookmark`
- `GET /bookmarks`

## 4. 数据库

- 使用 SQLite 文件数据库：`claw_ai.db`
- 首次启动自动建表

## 5. 下一步建议

- 接入真实 LLM 推理服务替换 `build_assistant_reply`
- 为 Skill 执行增加异步队列和超时重试
- 增加流式回复（SSE/WebSocket）
- 增加权限体系与团队空间隔离

## 6. 模型与 Agent 配置

推荐直接在 App 内点「设置」保存配置（持久化到 `~/.contextedai/settings.json`）。
也可用环境变量：

```bash
export MODEL_API_BASE_URL="https://your-provider.com/v1"
export MODEL_API_KEY="your_api_key"
export MODEL_NAME="your-model-name"
export MODEL_LIST="gpt-4o-mini:GPT-4o Mini,claude-3-5-sonnet:Claude Sonnet"
export AGENT_ENGINE="claw_native"  # 或 openclaw_compatible
export OPENCLAW_API_BASE_URL=""
export OPENCLAW_API_KEY=""
```

也兼容常见变量名：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`

`openclaw_compatible` 模式下，如果配置了 `OPENCLAW_API_*`，会优先走 OpenClaw 兼容路由；否则自动回退默认模型路由。

注意：系统不再内置默认模型候选列表。未配置模型时，模型下拉会显示“未配置模型”，并阻止发送请求。

本地 ClawHub Skill（可选）：

```bash
export CLAWHUB_LOCAL_SKILLS_DIR="/absolute/path/to/your/downloaded/skills"
```

配置后：

- `GET /skills` 会扫描本地目录下的 Skill 清单（`skill.json` / `manifest.json` / `clawhub.json`）
- 发送消息时会在对应 Skill 目录执行 `command` 或 `entrypoint`（支持 `.py`/`.js`/可执行文件）

未配置 `CLAWHUB_LOCAL_SKILLS_DIR` 时，也会自动尝试以下默认目录：

- `~/.openclaw/skills`
- `~/.config/openclaw/skills`
- `~/Library/Application Support/OpenClaw/skills`（macOS）
- `~/.clawhub/skills`
- `~/.config/clawhub/skills`
- `./skills`
- `./.openclaw/skills`
- `./.clawhub/skills`
