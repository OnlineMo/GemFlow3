# DeepResearch 自动化说明（GemFlow3 → DeepResearch-Archive）

这个项目把“每日热点研究”这件事拆成两部分：GemFlow3 负责抓热点、生成报告并推送；DeepResearch-Archive 负责存放与展示报告。

---

## 快速开始（本地 dry‑run）

1) 安装依赖

```bash
pip install -r Flow/requirements.txt
```

2) 复制环境变量模板并按需修改

```bash
cp Flow/.env.example .env
```

关键项（最少要改的）：
- REPO_B=owner/DeepResearch-Archive
- DEEPRESEARCH_BASE_URL=http://localhost:8123  # 指向后端引擎
- TZ=Asia/Shanghai

3) 运行（不推送 DeepResearch-Archive）

```bash
DRY_RUN=1 python Flow/main_workflow.py
```

入口脚本：[Flow/main_workflow.py](Flow/main_workflow.py)

---

## 项目结构

- 编排（GemFlow3）
  - 主流程：[Flow/main_workflow.py](Flow/main_workflow.py)
  - 配置加载：[Flow/src/config.py](Flow/src/config.py)
  - 引擎客户端：[Flow/src/engine_client.py](Flow/src/engine_client.py)
  - 热榜抓取与缓存：[Flow/src/trends.py](Flow/src/trends.py)
  - 主题提取与分类：[Flow/src/topics.py](Flow/src/topics.py)
  - GitHub 写入：[Flow/src/github_api.py](Flow/src/github_api.py)
  - 导航与首页渲染：[Flow/src/renderers.py](Flow/src/renderers.py)
  - 工具与日志：[Flow/src/utils.py](Flow/src/utils.py)、[Flow/src/logger.py](Flow/src/logger.py)
- 引擎（LangGraph + Gemini）
  - 图定义与服务端：[gemini-fullstack-langgraph-quickstart/backend/src/agent/graph.py](gemini-fullstack-langgraph-quickstart/backend/src/agent/graph.py)、[gemini-fullstack-langgraph-quickstart/backend/src/agent/server.py](gemini-fullstack-langgraph-quickstart/backend/src/agent/server.py)
  - 模型选择与统一开关：[gemini-fullstack-langgraph-quickstart/backend/src/agent/configuration.py](gemini-fullstack-langgraph-quickstart/backend/src/agent/configuration.py)
  - 容器与编排：[gemini-fullstack-langgraph-quickstart/Dockerfile](gemini-fullstack-langgraph-quickstart/Dockerfile)、[gemini-fullstack-langgraph-quickstart/docker-compose.yml](gemini-fullstack-langgraph-quickstart/docker-compose.yml)
- CI
  - 每日任务：[.github/workflows/daily-deepresearch.yml](.github/workflows/daily-deepresearch.yml)

---

## 配置项（建议放到 GitHub Actions 的 Vars/Secrets）

必填（Vars）
- REPO_B：目标仓库坐标（owner/repo）
- DEEPRESEARCH_BASE_URL：研究引擎基址（例如 http://localhost:8123）
- TZ：时区（Asia/Shanghai）

Secrets
- REPO_B_TOKEN：DeepResearch-Archive 的 PAT（contents: write）
- GEMINI_API_KEY：仅当 CLASSIFIER_KIND=gemini 且未提供 CLASSIFIER_TOKEN 时作为回退
- CLASSIFIER_TOKEN：分类后端的 token（优先于变量）
- LANGSMITH_API_KEY：可选，仅示例容器使用

可选（Vars）
- CLASSIFY_WITH_AI：是否启用 AI 分类（默认 false）
- CLASSIFIER_KIND：gemini | openai_compat | service（默认 gemini）
- CLASSIFIER_BASE_URL：分类服务地址；留空按 KIND 使用官方默认
- CLASSIFIER_MODEL：分类模型名（默认 gemini-2.0-flash）
- GEMINI_MODEL：引擎统一模型开关（默认 gemini-2.5-flash）

Token 解析顺序（分类）
1) CLASSIFIER_TOKEN（Secrets/Vars）
2) 当 KIND=gemini 时回退 GEMINI_API_KEY
3) 其他情况留空（退回关键词规则分类）

相关实现：[Flow/src/topics.py](Flow/src/topics.py)、[.github/workflows/daily-deepresearch.yml](.github/workflows/daily-deepresearch.yml)、[Flow/src/config.py](Flow/src/config.py)

---

## 工作原理（一句话版本）

1) 抓热点 → 2) 提取候选 → 3) 去重并确定版次 → 4) 分类 → 5) 调后端引擎生成 Markdown → 6) 推送到 DeepResearch-Archive → 7) 更新导航/首页。

细节：
- 热榜缓存：当天结果写入 Flow/daily_trends/{yyyy-mm-dd}.json，24h 内优先使用缓存。
- 去重：topic 归一 + 日期 + 版次 → SHA-256 指纹。
- 命名：{slugified_主题}-{日期}--v{版次}.md；分类目录为安全 slug。
- 导航与首页：扫描 DeepResearch-Archive 的 AI_Reports 目录生成 NAVIGATION.md 与 README 最新区块。

---

## 报告命名与分类

- 存储路径：AI_Reports/<分类slug>/<slugified_主题>-<yyyy-mm-dd>--vN.md
- 分类清单（默认顺序，可通过 CATEGORY_LIST 覆盖）：
  人工智能和机器学习 / 大型语言模型 / 软件开发与工程 / 网络安全 / 云和 DevOps / 数据和数据库 / 网络和移动 / 消费电子和硬件 / 游戏与互动 / 区块链与加密 / 科学与太空 / 医疗保健与生物技术 / 能源与气候 / 经济与市场 / 政策与法规 / 行业与公司 / 文化与媒体 / 未分类
- 分类策略：
  - 启用 AI 分类时：按 KIND 路由至 gemini/openai_compat/service；失败回退到规则匹配与“未分类”
  - 未启用：仅规则匹配与回退

---

## GitHub Actions 工作流

- 文件：[.github/workflows/daily-deepresearch.yml](.github/workflows/daily-deepresearch.yml)
- 关键点：
  - 拉取代码 → 安装依赖 → 构建并启动引擎容器 → 健康检查 → 运行 GemFlow3 → 采集日志 → 关停
  - 模型开关通过 Vars.GEMINI_MODEL 传递到容器；Flow 侧使用 DEEPRESEARCH_BASE_URL 调用
  - 分类相关 Vars/Secrets 会透传给 Flow 进程（详见上文“配置项”）

---

## 常见问题 / 故障排查

- 引擎起不来
  - 看容器日志：`docker logs --tail 200 langgraph-api`
  - 健康检查地址：/healthz 或 /openapi.json
- 没有生成新报告
  - 可能当天热榜为空，或候选不足；查看 [Flow/state/logs/*.jsonl](Flow/state)（目录会在运行时生成）
- 推送失败
  - 确认 REPO_B_TOKEN 作用于目标仓库且仅 contents: write 权限
- 导航/首页不更新
  - 渲染异常不会影响单篇报告保存；看流程日志与 DeepResearch-Archive 目录结构

---

## 变更记录

- 2025‑08
  - 支持自定义 DeepResearch BaseURL（DEEPRESEARCH_BASE_URL）
  - 新增可选 AI 分类：gemini/openai_compat/service；明确 token 回退（仅 gemini）
  - 将分类/引擎相关变量暴露到 CI（Vars/Secrets），便于切换
  - README 与本文档梳理为“可落地说明”，减少术语堆砌

---

## 附：示例文件/入口

- 主流程入口：[Flow/main_workflow.py](Flow/main_workflow.py)
- 引擎服务端入口：[gemini-fullstack-langgraph-quickstart/backend/src/agent/server.py](gemini-fullstack-langgraph-quickstart/backend/src/agent/server.py)
- 配置与分类实现：[Flow/src/config.py](Flow/src/config.py)、[Flow/src/topics.py](Flow/src/topics.py)
