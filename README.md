# AIGC Workbench

F01 工程基础：React 工作台 Shell + FastAPI 模块化单体 + SQLite。
当前仅实现 **Quick-A Task 草稿创建、列表、查询与恢复**，不具备图片制作条件。

- **F01-A ENGINEERING VERIFIED**：范围限于下述工程链路与已执行检查。
- **F01-B UI BASELINE PENDING**：已核实区域的 Shell 已实现并通过桌面浏览器查看；旧仓带数据候选、历史版本、修改草稿、Pass / Reject 和 Inspector 的完整视觉基线仍未完成。
- 没有真实 AI、图片生成、素材上传、完整图位或审核功能；没有开始 S01。
- 业务与技术设计依据仍位于 [docs/business](docs/business/) 和 [docs/implementation](docs/implementation/)，本轮没有重写这些文档。

## 安装与启动

需要 Python 3.12+、Node.js 20.19+ 或 22.12+。本轮使用 Python 3.12.13、Node 26.4.0、npm 11.17.0。

在仓库根目录安装：

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.lock
backend/.venv/bin/python -m pip install --no-deps -e backend
npm --prefix frontend ci
```

后端终端，在仓库根目录：

```bash
backend/.venv/bin/python -m uvicorn aigc_workbench.app:create_app --factory --host 127.0.0.1 --port 8022
```

另一个终端，在仓库根目录：

```bash
npm --prefix frontend run dev
```

打开 `http://127.0.0.1:8021/`；后端健康检查为 `http://127.0.0.1:8022/api/health`，接口文档为 `http://127.0.0.1:8022/docs`。前端通过同源 `/api` 访问 Vite 的本机后端代理。
本轮检查时 8020 已由其他进程占用，故使用 8022，未停止原进程或旧仓服务。端口被占用时先核对进程，不自动终止其他服务。

Ctrl+C 分别停止本轮启动的前后端。后端不使用自动重载；修改 Python 后显式重启。生产构建检查使用 `npm --prefix frontend run build`；停止 dev 后可用 `npm --prefix frontend run preview` 本地查看构建产物，不能将此视为生产部署。

## 配置和数据

[.env.example](.env.example) 只有可提交的示例值，不含凭据。后端读取进程环境，不自动加载环境文件：

| 配置 | 默认值 / 用途 |
|---|---|
| `AIGC_DATA_DIR` | `.runtime/f01`；相对路径从仓库根目录解析 |
| `AIGC_ALLOWED_ORIGINS` | 本机前端 8021 的 Origin；逗号分隔 |
| `AIGC_API_TARGET` | `http://127.0.0.1:8022`；Vite 服务端代理目标 |

数据库为 `.runtime/f01/workbench.sqlite3`，运行数据、虚拟环境、依赖、构建产物、缓存和本地环境文件均被 Git 忽略。本轮没有商业素材文件；素材文件存储留待 S01 实际需要时实现，不创建空 Asset 存储模块。

这是仅绑定回环地址的开发环境：写请求检查 Origin，拒绝非本机 Host，但**未实现多用户身份认证及资源授权**，不能直接用于运营 Pilot 或公网服务。模型凭据完全不需要，不得放入前端 `VITE_*` 变量；当前没有模型客户端或付费入口。

## 本轮草稿契约

本次任务明确选择“尚不具备制作条件的 Quick-A Task 草稿”。[T01](docs/business/architecture/T01_SYSTEM_BOUNDARY.md) 将 Task 视为业务容器，[I02](docs/implementation/I02_ENGINEERING_BOUNDARIES.md) 分开定义 CreateTask、SaveAssets 和 StartCreation；未发现禁止在准备阶段先保存标识的条款。

因此只保存任务 ID、名称、Quick-A 模式、草稿状态和创建时间。名称 1～120 字符是本接口的标识校验，不是新增营销规则。Domain 固定返回 `status=draft`、`can_generate=false`；商品图和营销参考仍是制作输入前提，不能用此草稿绕过 [01](docs/business/01_BUSINESS_MODES.md) / [04](docs/business/04_IMAGE_CREATION_WORK_METHOD.md)。请求不得自行指定模式、就绪状态或生成权限。

| 实际接口 | 用途 |
|---|---|
| `GET /api/health` | 应用启动健康状态 |
| `POST /api/tasks` | JSON `{"title":"任务名称"}`，返回 201 和稳定 UUID |
| `GET /api/tasks` | 查询已保存草稿 |
| `GET /api/tasks/{uuid}` | 恢复具体草稿 |

没有 generate / review / upload / AI 接口。错误统一使用 `{"error":{"code":"...","message":"..."}}`；无效请求 422、不存在草稿 404、来源拒绝 403、存储不可用 503、未预期错误 500。前端不会把请求失败显示为保存成功。

## 工程职责及真实调用路径

| 位置 | 已实现责任 |
|---|---|
| [frontend/src/pages](frontend/src/pages/) | 任务中心、按 URL 从服务器恢复工作台 |
| [TaskForm.tsx](frontend/src/components/TaskForm.tsx) | 本地名称、保存中 / 错误状态；只在提交时写入 |
| [WorkspaceShell.tsx](frontend/src/components/WorkspaceShell.tsx) | INPUT / OUTPUT 布局、折叠状态、明确禁用的未实现区域 |
| [frontend/src/api/tasks.ts](frontend/src/api/tasks.ts) | 唯一 Task HTTP 访问及响应格式检查 |
| [api.py](backend/src/aigc_workbench/api.py) | 请求类型、响应模型、路由与依赖获取 |
| [application.py](backend/src/aigc_workbench/application.py) | TaskService 用例；TaskStore 仅隔离实际 SQLite 外部依赖 |
| [domain.py](backend/src/aigc_workbench/domain.py) | 草稿身份、名称规范化、不可制作不变量；只依赖标准库 |
| [persistence.py](backend/src/aigc_workbench/persistence.py) | SQLite 初始化、事务、参数化保存与读取 |
| [app.py](backend/src/aigc_workbench/app.py) / [config.py](backend/src/aigc_workbench/config.py) | 组装、显式启动初始化、本机访问边界、错误约定与配置 |

Context / Effective State / Context Assembly、AI Capabilities、Runtime 的 Run / Attempt、模型工具适配和 Execution Evidence 的责任继续以 I02 为准，**本轮未实现，不创建空包、假运行器或通用 Registry**。后续有真实调用方时才增加代码。Task 数据不是 Runtime Run，浏览器临时状态不是业务权威。

| 调用环节 / 函数 | 输入 → 输出 | 主要副作用 |
|---|---|---|
| `TaskForm.submit` | 本地名称 → 创建请求 | 显式提交，显示保存中；失败保留输入 |
| `createQuickADraft` | title → `POST /api/tasks` JSON → TaskDraft | 一次真实 HTTP 写请求，不自动重试 |
| `api.create_task` / `CreateTaskRequest` | 校验后的请求 → TaskService | HTTP 校验与序列化，不拼 Prompt |
| `TaskService.create_draft` | title → Domain 草稿 → 已保存对象 | 调用实际存储端口 |
| `TaskDraft.create` | title → UUID、规范化名称、UTC 时间、不可制作草稿 | 无数据库、Web 或模型依赖 |
| `SQLiteTaskStore.save` | 草稿 → SQLite 记录 | 参数化 INSERT，事务提交后才返回 |
| `TaskResponse.from_task` | Domain → HTTP 201 JSON | 无业务状态更新 |
| `TaskForm` → `navigate` → `Workspace` | 稳定 ID → `/workspace/{id}` | URL 记录恢复身份，随后 GET |
| `getTask` → `api.get_task` → `TaskService.get_draft` → `SQLiteTaskStore.get` | UUID → 实际记录 → 页面 | 只读查询；刷新或后端重启后同一路径恢复 |

按实际功能拆分页面、用例、纯业务规则与 SQLite；没有为每个函数建立多层包装。唯一 Protocol 有真实数据库调用方，未机械创建九套专题模块。

## 数据库初始化及后续变更

只有 `task_drafts` 表，未预建 Asset / Slot / Version / Review / Run 模型。应用启动时，在 `BEGIN IMMEDIATE` 事务中检查 `PRAGMA user_version`：空库创建版本 1；版本 1 保留数据；未知版本拒绝启动，不自动清空或降级。

后续 Schema 变更需要在 `SQLiteTaskStore.initialize` 增加明确的顺序迁移、升级版本号及迁移 / 保留数据测试，先备份已有数据库；不能通过删除数据库代替迁移。每个读写操作使用独立连接，连接显式关闭，写入在事务提交后才返回。

## 检查和实际验证

```bash
backend/.venv/bin/python -m pytest backend/tests
backend/.venv/bin/python -m pip check
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

本轮后端 20 项测试、前端 8 项测试、TypeScript 检查和生产构建通过。后端测试覆盖真实临时 SQLite、创建 / 查询 / 新应用恢复、无效输入不落库、未知 Schema 拒绝、存储错误、来源边界及不存在的生图入口。组件测试明确使用 HTTP mock，不能冒充浏览器、AI 或业务验收。

实际运行验证使用独立新端口 8021 / 8022，在 Microsoft Edge 中完成真实创建、刷新恢复；随后停止并重启本轮 Uvicorn 进程，浏览器和 API 仍读取相同 UUID、名称、草稿状态及创建时间。真实 API 另核对空白名称 / 伪造就绪状态为 422，生图请求为 404，失败请求未新增记录。无商业数据的运行记录留在被忽略的 `.runtime/f01-verification.json`，不是 AI Execution Evidence。

旧仓来源为 I01 §8 记录的 `commerce-image-poc`：`main@371e3774…`，包括未提交模板差异。只读复核了模板和相关 CSS；沿用顶部导航、任务中心、可折叠浅色 INPUT、图位级参考布局及深色 OUTPUT。新 Shell 的禁用控件和草稿标识是本轮范围所需的明确差异，没有复制旧全局 JS、Planning 门禁或实验面板。

已在桌面浏览器查看任务中心和恢复后的 INPUT，并核对 OUTPUT 空态与禁用控制；旧仓可用带数据只读页面仍缺失，历史 / 修改 / 审核 / Inspector 视觉对照未完成。截图仅在本轮工具会话中查看，未提交浏览器标签、商业资料或截图文件。完整响应式、键盘 / 辅助技术验收及运营 UI 接受尚未完成。

**状态分别记录：F01-A ENGINEERING VERIFIED；F01-B UI BASELINE PENDING。** 不声明 F01 FULLY ACCEPTED，不将模型成功或工程测试等同业务接受。后续只按新授权进入 S01。
