# AIGC Workbench

React 工作台 + FastAPI 模块化单体 + SQLite / 本地原始素材文件。
当前接通 Quick-A 新任务从素材上传、按需理解和制作决定到单图候选，以及指定版本修改、人工审核及恢复。

- **S01-A ENGINEERING VERIFIED**：Task 演进、V1→V2、Asset/Slot、幂等写入与 R01～R04。
- **S01-B ENGINEERING VERIFIED**：范围限于本轮实际接通并在桌面浏览器验证的素材上传、图位参考绑定、保存/刷新/重新打开/进程重启恢复。
- 这不是 BUSINESS ACCEPTED；完整旧 UI、带数据候选、历史版本、修改、Pass / Reject、Inspector 仍未完整视觉验收。
- **S02 ENGINEERING VERIFIED**：受控 CLI 完成 C01 输入准备、四层 Prompt、快照及测试证据。
- **S03 第一阶段**：固定实验 CLI 和真实 C01 接口继续保留；历史真实调用以 Run/Evidence 为准。本轮 P2-B1 集成仅用隔离测试适配器验证，不新增真实 AI 评价。
- 当前工作台已接通 Quick-A 单图生成、指定版本修改及人工审核；Quick-B / Listing 仍未开放。下方 S01～S03 实验说明保留其当时范围，当前集成见文末 P2-B1。
- [业务/架构依据](docs/business/)和[实施文档](docs/implementation/)未在本轮重写。

## 安装与启动

需要 Python 3.12+、Node.js 20.19+ 或 22.12+。本机验证使用 Python 3.12.13、Node 26.4.0、npm 11.17.0。

在仓库根目录安装：

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.lock
backend/.venv/bin/python -m pip install --no-deps -e backend
npm --prefix frontend ci
```

两个终端分别运行：

```bash
backend/.venv/bin/python -m uvicorn aigc_workbench.app:create_app --factory --host 127.0.0.1 --port 8022
```

```bash
npm --prefix frontend run dev
```

工作台：`http://127.0.0.1:8021/`；健康检查：`http://127.0.0.1:8022/api/health`；API 文档：`http://127.0.0.1:8022/docs`。
前端通过同源 `/api` 代理访问本机后端。8020 原有进程未动；端口占用时先核对，不停止其他服务。
Ctrl+C 分别停止本轮前后端。Python 修改后显式重启；不使用自动重载。
生产构建检查用 `npm --prefix frontend run build`；停止 dev 后可用 `npm --prefix frontend run preview` 本地检查构建产物，不代表生产部署。

## 配置及运行数据

[.env.example](.env.example) 只有示例，不含凭据。普通后端启动读取进程环境；C01 的 credential-status / check-model-access / preflight-real / run-real 命令在开发入口校验后，仅显式加载后端私有凭证文件，见下文。

| 配置 | 默认值 / 用途 |
|---|---|
| `AIGC_DATA_DIR` | `.runtime/f01`，相对仓库根目录解析；沿用 F01 数据目录 |
| `AIGC_ALLOWED_ORIGINS` | 本机 8021 的 Origin，逗号分隔 |
| `AIGC_API_TARGET` | `http://127.0.0.1:8022`，Vite 服务端代理 |
| `AIGC_ENV` | `development`；C01 CLI 仅允许该环境 |
| `AIGC_ENABLE_C01_TESTING` | `0`；显式为 `1` 才能使用 C01 开发 CLI |
| `AIGC_ENABLE_C01_REAL` | `0`；真实调用的独立开关；还需 CLI 付费确认及预算 |
| `OPENAI_API_KEY` | 无默认值；有效进程变量优先，否则 C01 开发命令读取 `backend/.env.local` |

目录内包含 `workbench.sqlite3`、`staging/<UUID>.part`、`assets/<UUID>`。
数据库、原始商业素材、备份、验证记录、环境文件、虚拟环境、依赖、缓存及构建产物不纳入 Git。
S02 测试及 S03 离线预检不需要模型凭据；S03 单次 HTTPS 适配器仅访问官方端点，不得将密钥放入 `VITE_*`。

服务只绑定回环地址，校验 Host 和写请求 Origin。没有跨域 CORS 开放，也没有多用户认证或资源授权，不能直接用于运营 Pilot 或公网服务。
Task/Asset/Slot 的资源归属在服务器校验；这不等同于用户身份认证。

## 当前业务范围和状态

Task 是同一个业务身份：Domain 从受限 `TaskDraft` 演进为 `Task`，旧名称仅为同一类的兼容别名。
保留 `task_drafts` 表及所有旧 UUID、名称、模式、状态和时间，不复制“正式 Task”。

Quick-A 继续采用实际商品图及同款同配置营销参考，不要求 Listing；业务权限以 [01](docs/business/01_BUSINESS_MODES.md)、[02](docs/business/02_INTENT_AND_CONTEXT_RULES.md)、[04](docs/business/04_IMAGE_CREATION_WORK_METHOD.md) 为准。
资料保存、模型观察、候选生成和人工审核分别留证；不因上传或模型成功就认证商品事实、营销宣称或运营接受。

`status=draft` 不因上传而变成制作完成。每个 Slot 的 `inputs_ready` 由服务器根据该 Task 的商品素材和已保存营销参考绑定派生。
`materials_ready` 表示至少一个独立图位基础图片输入齐备，不表示所有图位齐备；未绑定图位不阻断其他图位。
Task DTO 中的 `can_generate=false` 保留准备资料接口兼容性；实际生成条件由图位 `/workspace` 接口的 `readiness.can_generate` 负责，不能从素材齐备状态直接推断。

支持主图、对比图、卖点图、使用场景图的最小创建、参考替换/解除绑定、输出尺寸及逐图要求保存。
主图和对比图各最多一个，动态类型可多次创建；排序不是身份。
尺寸图专用素材/确认流程、图位删除、素材删除、整体要求、完整 Quick-B/Listing 留待后续，界面明确未接通。

## API 和安全重试

| 接口 | 用途 |
|---|---|
| `GET /api/health` | 应用健康检查 |
| `POST /api/tasks` | JSON `{title, operation_id}`，返回 Task |
| `GET /api/tasks` / `GET /api/tasks/{id}` | 列表/完整恢复 |
| `POST /api/tasks/{id}/assets` | 原始文件 body；Query 为 role、operation_id、filename |
| `GET /api/tasks/{id}/assets/{asset_id}/content` | 归属及 SHA-256 校验后读取 |
| `POST /api/tasks/{id}/slots` | JSON `{kind, operation_id}` |
| `PUT /api/tasks/{id}/slots/{slot_id}` | 显式保存 reference_asset_id、output_size、requirements |

创建/上传/图位创建使用客户端 UUID 操作身份，同一操作在数据库事务中串行检查并复用原结果；同键不同内容返回 409。
任务名和文件哈希不是去重身份；同名任务、同文件的独立上传操作允许拥有独立身份。
参考配置 PUT 是显式幂等赋值，不创建新 Slot，也不覆盖原文件。

前端区分明确拒绝和结果未知；网络中断、无法解析成功响应或 5xx 不表示服务器撤销。
“安全重试”复用同一操作身份和原内容，不自动重试。
Task 创建的未确认意图保存在 sessionStorage，刷新后只恢复意图，不自动 POST，也不把它当作已保存 Task；浏览器禁用该存储时仅保证当前页面内复用。
上传 File/操作身份保留在当前页面，离开后先查询已保存素材，不自动重传。

错误格式：`{error:{code,message}}`；非法输入 422、体积超限 413、冲突 409、资源不存在 404、来源拒绝 403、存储故障 503、其他内部错误 500。
没有 generate / review / context / AI 执行接口。

## SQLite 升级及文件一致性

启动时显式初始化/迁移：F01/S01 的 V1→V2 新增 task_creations、assets、image_slots；S02 V3 追加 C01 快照与测试执行证据；S03 V4 只追加 c01_model_runs 及终态保护，不重建或改写原任务表。
迁移在单一事务中进行，成功才升级 user_version；未知版本拒绝，异常回滚。每次运行期连接启用外键，且使用 `mode=rw`，缺库读取不会隐式建库。
后续 Schema 变更继续采用顺序迁移、备份和旧数据保留测试；禁止删除数据库代替迁移。

本次实际运行库在停止 F01 后备份到被忽略的 `.runtime/s01-backups/`，再启动升级；原 F01 Task 字段已逐项对照保留。

文件由服务器生成 UUID 路径，原文件名仅作为元信息。单图限制 10 MiB、2500 万像素，接受静态 JPEG/PNG/WebP；
校验实际格式、声明类型和完整解码，保存原始字节及 SHA-256，不重编码或覆盖。
解析依据见 [Pillow Image API](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.verify)。

上传链路：限流读取完整请求 → 校验/暂存并 fsync → DB 写事务/归属及幂等检查 → 原子无覆盖发布文件并同步目录 → 提交 DB。
提交前失败回滚业务记录并清理本次暂存；中断的请求不会将本地预览显示为已保存。
发布后若提交结果不明，不删除可能已经提交的文件，不返回成功。无关联的 UUID 文件和遗留 staging 文件由 `orphan_files()` 识别，启动日志报告，保留供核查，不自动删除。
重复上传若已有提交，会核查原文件完整性并返回原 Asset；文件缺失/损坏明确报错。
读取路径仅由 UUID 生成，拒绝符号链接，按记录的长度和 SHA-256 校验后返回图片。

## 模块职责和真实调用路径

| 位置 | 责任 |
|---|---|
| [Workspace.tsx](frontend/src/pages/Workspace.tsx) | 按 URL 加载；工作台内串行写操作、服务器快照 |
| [AssetUploader.tsx](frontend/src/components/AssetUploader.tsx) | 待上传 File/本地预览、原始文件提交、Object URL 释放 |
| [SlotPanel.tsx](frontend/src/components/SlotPanel.tsx) | 独立图位、参考选择、未保存配置与显式保存 |
| [useWriteAction.ts](frontend/src/hooks/useWriteAction.ts) | 单次写入、页面代次保护、未知结果与显式重试 |
| [api/tasks.ts](frontend/src/api/tasks.ts) | 唯一 HTTP 客户端、DTO 检查 |
| [api.py](backend/src/aigc_workbench/api.py) | 请求/响应、上传流字节上限、线程池调用用例 |
| [application.py](backend/src/aigc_workbench/application.py) | TaskService：创建、上传、绑定及事务/文件协调 |
| [domain.py](backend/src/aigc_workbench/domain.py) | Task/Asset/Slot 身份、角色/归属、图位限制及输入状态 |
| [persistence.py](backend/src/aigc_workbench/persistence.py) | SQLiteTaskStore / SQLiteChanges：迁移、快照、真实写事务 |
| [file_storage.py](backend/src/aigc_workbench/file_storage.py) | 真实图片验证、暂存、无覆盖发布、完整性读取 |
| [app.py](backend/src/aigc_workbench/app.py) / [config.py](backend/src/aigc_workbench/config.py) | 组装、显式初始化、配置和统一错误 |

创建：TaskForm → createQuickADraft → api.create_task → TaskService.create_draft → Task.create / 事务幂等检查 → SQLiteChanges.add_task → DTO → URL → getTask。

上传：AssetUploader → uploadAsset → api.upload_asset（实际 bytes）→ TaskService.upload_asset → LocalImageFiles.stage → SQLiteChanges / Asset 角色 → publish → 提交 → TaskResponse → 工作台服务器快照。

绑定：SlotEditor → configureSlot → api.configure_slot → TaskService.configure_slot → ImageSlot.configure → SQLiteChanges.update_slot → DTO → 工作台。

Domain 不依赖 FastAPI、SQLite 或模型 SDK。事务及文件端口都有实际调用方；没有创建 Context/AI/Runtime 空包或通用 Registry。

## 检查与验证边界

```bash
backend/.venv/bin/python -m pytest backend/tests
backend/.venv/bin/python -m pip check
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

本轮后端 48 项、前端 18 项、类型检查与构建通过。后端包含真实临时 SQLite/文件、V1 升级及失败回滚、并发幂等、跨 Task 拒绝、格式/体积/解码、缺库 GET、原文件哈希及符号链接测试。
文件/DB 失败、提交确认丢失和上传中断采用明确的故障注入或 ASGI 模拟；前端使用 HTTP mock 验证过期成功/失败、结果未知重试、预览释放及保存绑定，不能冒充真实网络故障实测。另在本机 HTTP 验证了创建重放/409、跨 Task 绑定拒绝，并用真实 TCP 半关闭中断上传，未新增素材。

Microsoft Edge 实际上传了商品 JPEG 和营销参考 PNG，保存到原 F01 Task；真实缩略图、参考绑定、单图要求、刷新、任务中心重新进入及后端进程重启后恢复通过。
实际下载字节 SHA-256 与旧仓只读输入副本一致。记录、素材及备份只在忽略的 .runtime 内；截图仅在本轮工具会话查看，未提交 Git。
未模拟真实磁盘耗尽、断电或恶意高并发；本机安全边界不等于运营发布安全验收。

旧 UI 来源为 I01 §8 的 commerce-image-poc。只读查看现有 8013 新建页，切换 Quick-A，核对浅色 INPUT、固定三列图位、动态组及未保存参考缩略图/移除交互，没有保存旧任务或触发 AI。
新仓保留这些区域关系；增加明确的服务端素材区与图位选择，按本次“先保存素材、再独立绑定”的要求区分资产和图位身份；不继承旧全局脚本、另存 Task 方式或 Planning 门禁。
候选/历史/修改/Pass/Inspector 的完整旧带数据视觉基线仍待补齐；完整响应式及辅助技术验收尚未完成。

## S02：C01 输入准备与测试证据（开发 CLI）

**S02 ENGINEERING VERIFIED**，限于真实业务来源 → 有效状态 → Context → 四层 Prompt → 不可变 Snapshot → TEST_ONLY Run / Attempt / Evidence。
**C01 AI EVALUATED：未完成；BUSINESS ACCEPTED：未建立。** S02 本身没有真实模型调用；S03 另见下文。

采用本地 CLI，不新增开发 HTTP API、工作台页面或完整 Inspector。
入口在任何存储访问前检查 `AIGC_ENV=development` 且 `AIGC_ENABLE_C01_TESTING=1`；默认关闭。生产环境即使开关为 1 也拒绝。
`.env.example` 不自动加载，以下环境值仅作用于该命令。S02 run 始终使用测试适配器，不读取真实模型开关或凭据。

将下列 TASK_UUID / PRODUCT_ASSET_UUID / SNAPSHOT_UUID / OPERATION_UUID / RUN_UUID 替换为实际 UUID：
```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 backend/.venv/bin/python -m aigc_workbench.c01_cli prepare --task-id TASK_UUID --asset-id PRODUCT_ASSET_UUID

env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 backend/.venv/bin/python -m aigc_workbench.c01_cli inspect-snapshot --snapshot-id SNAPSHOT_UUID --format readable

env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 backend/.venv/bin/python -m aigc_workbench.c01_cli run --snapshot-id SNAPSHOT_UUID --operation-id OPERATION_UUID

env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 backend/.venv/bin/python -m aigc_workbench.c01_cli inspect-run --run-id RUN_UUID

env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 backend/.venv/bin/python -m aigc_workbench.c01_cli history --task-id TASK_UUID
```

prepare 返回完整 JSON 和 Snapshot ID。inspect-snapshot 默认 JSON，readable 展示逐层文字、最终计划输入及单独图片关联。
run 只执行 `TEST_ONLY / MOCK_ADAPTER`；`--adapter-mode fail` 是显式失败演示，不是模型失败。
同 operation UUID 重放只返回原 Run/Attempt；不同快照或测试模式冲突，不自动重跑。
成功退出码 0，已记录的失败 Run 退出码 1，入口/准备/存储/查询错误退出码 2；错误不会输出伪成功记录。

### 实际代码位置

| 文件 | 实际职责 |
|---|---|
| [c01_cli.py](backend/src/aigc_workbench/c01_cli.py) | 开发开关、命令、非零错误退出 |
| [c01_context.py](backend/src/aigc_workbench/c01_context.py) | select_source / resolve_effective_state / assemble_context |
| [c01_prompt.py](backend/src/aigc_workbench/c01_prompt.py) | compose_prompt / validate_composition / readable_request；模板 c01-four-layer-v1 |
| [c01_execution.py](backend/src/aigc_workbench/c01_execution.py) | C01Service.prepare / run_test；实际 C01TestAdapter |
| [c01_persistence.py](backend/src/aigc_workbench/c01_persistence.py) | 快照图片、只追加记录、Run/Attempt/Evidence 一致性 |

### 来源、Context 与 Prompt

- 显式选择当前 Quick-A Task 中的 product_image；校验归属、角色、文件存在、字节长度及 SHA-256，不默认选营销参考。
- Effective State 固定 C01 视觉观察范围。Task.mode 仅作业务模式声明；不是性能认证。
- 当前 S01 没有商品身份/配置确认记录，`operator_confirmed_information=[]`；不把图位制作要求或 Task 名称变成确认事实。
- Context 分开保存 business_source、operator_confirmed_information、applicable_rules、capability_specific_inputs。
- 规则映射记录业务文档/研究章节、状态及文件哈希。代码中固定的技术约束仍是实现候选，不把研究建议升级为业务批准。
- 业务来源中的 Task 名称、文件名仅用于追溯，不进入计划模型文字；参考图内容、其他商品图、Slot 要求和历史 AI 结果不进入本次 C01。
- Prompt 四层：Capability Instruction、Rules / Boundaries、Effective Context、Output Contract。四层可独立检查，但只形成一个计划请求。
- 输出契约为六个 string 数组：visible_structure、components、visible_text、spatial_relations、inferences、uncertainties。前四项只记录直接观察，推断与不确定性分开。
- 模板版本、各层内容/来源/哈希、动态 Context 哈希、最终文字与图片 Asset ID/SHA 均固定在 Snapshot。
- 来源变化后新 prepare 重新读取和解析；旧快照不改写。不实现自动失效、语义规则同步或缓存平台；规则来源变化时需维护代码中的规则映射。

### 不可变输入及执行记录

V2→V3 只新增 c01_snapshots、c01_runs、c01_attempts、c01_evidence 和保护约束，不更新业务行。
Snapshot JSON 与 Evidence 仅追加，数据库触发器禁止 UPDATE/DELETE，终态 Run/Attempt 不再改写；读取核对内容哈希及关联。
图片以 `c01/snapshot_images/<Snapshot UUID>` 固定原始字节副本，非新增业务 Asset；只写一次，设为只读，拒绝符号链接并核对 SHA-256。
复制后提交失败留下的 UUID 副本可由 history 的 orphan_snapshot_images 识别，不自动删除。

`PLANNED MODEL INPUT` 是未来 S03 的模型无关请求定义：文字与图片分别表达，provider/model 未配置，供应商序列化未实现。
测试适配器实际收到文字和图片 bytes，并独立计算输入收据；不是只收到文件路径字符串。
`ACTUAL TEST ADAPTER INPUT` 只证明本轮测试交接，不是 Actual Model Input；后者保持 null，model_api_call_attempted=false。
Test Adapter 不做图像识别，visual_understanding=null，不生成 AI_DERIVED 商品事实，不改变 Task/Slot/Pass。

每个 S02 Run 只有一个真实测试 Attempt，成功/失败及原始测试响应可查询。没有队列、自动重试或多 Attempt 恢复平台。
成功终态与 Evidence 在同一事务保存。若 Evidence 保存失败，尽可能记录 FAILED / MISSING，保留已知测试收据；连失败记录也无法保存则返回结果未确认。
遗留 RUNNING/PENDING 表示没有可靠终态，不保证进程仍活跃；检查现有记录，不把它当成功，不自动重跑。

### 验证与实际示例

本轮后端 86 项（含原 F01/S01 回归）、前端 18 项、TypeScript、构建和依赖检查通过。
新增测试覆盖身份/角色/跨 Task、缺失或损坏图片、来源选择期间变化、Context/Prompt 故障、四层一致性、
快照/证据不可变与篡改检测、适配器失败、证据保存/查询失败、状态矛盾、开发入口限制及迁移回滚。
文件/SQLite 使用真实临时资源；存储/适配器异常明确采用故障注入；一个测试禁止网络连接以验证测试路径无需网络。
原回归测试仅将最新 Schema 版本断言同步为 3，其业务保存、恢复、错误和禁用 AI 断言保留。

实际以 S01 已保存的商品 JPEG 完成 prepare、成功测试、显式失败测试、同操作重放、独立进程查询和 history。
本机实例在忽略的 `.runtime/s02-example/`：snapshot.json、planned-request.txt、successful-test-run.json、failed-test-run.json、history.json、verification.json。
迁移前备份在 `.runtime/s02-backups/`；V2→V3 后业务表逐行不变，固定图片与原图字节相等。
这些本地输出不是已提交的通用样本或业务批准文档。

S02 不能证明模型会关注图片、遵守 Prompt 或产生正确商品理解。S03 当前只准备下述接口，真实调用、理解效果和人工评价仍需后续明确授权。

## S03 第一阶段：真实 C01 接口与请求预检

**IMPLEMENTED / OFFLINE VERIFIED；REAL API NOT EXECUTED / NOT AI EVALUATED / NOT BUSINESS ACCEPTED。**
本阶段没有付费请求、真实供应商响应或费用账单；离线测试响应不作为商品理解成果。

### 接口、固定输入与费用依据

仅接入 OpenAI Responses：`https://api.openai.com/v1/responses`。候选显式模型 `gpt-4.1-mini-2025-04-14`，
[官方模型页](https://developers.openai.com/api/docs/models/gpt-4.1-mini)列明图片输入、Responses 和结构化输出。
这是一次受限 C01 实验的候选，不代表账户已获访问权限、最新或最优模型；没有自动模型替换。
使用 Python 标准库 urllib HTTPS 单次 POST，不新增 SDK 或依赖；显式使用环境代理，没有自动重试、重定向、批量、工具或额外上传调用。
拒绝设置了 `OPENAI_BASE_URL` 的真实执行，避免把官方端点与其他供应商混同。

固定使用 S02 Snapshot `8a72fce2-f08d-4a69-8f94-fb3ccc11a664`，
Asset `a452aaca-5bfe-4cb9-8bdb-9bd9bd3e8aa3`，
JPEG SHA-256 `cf304828c8204114c3a71aa69990d22848378b5a50b9330ad66900c2e0d3e554`。
原图及固定副本均为 448,920 bytes、1920×1920。预检核对当前 Task 归属/角色、真实文件、固定图片与完整 Snapshot 哈希；
不匹配就停止，既不重建旧快照，也不加入人工答案。`operator_confirmed_information=[]`。

四层 S02 文字原样作为一个 `input_text`；`input_image.image_url` 实际包含 `data:image/jpeg;base64,...` 的完整原始 JPEG，
不是只传路径、Asset ID 或 SHA。不重编码图片、不加入营销参考、不添加标准答案。
`text.format` 使用 Snapshot 原六字段 JSON Schema，`strict=true`、`additionalProperties=false`。
参数显式为上述模型、`detail=high`、`temperature=0`、`max_output_tokens=4096`、`service_tier=default`、`store=false`、`stream=false`。
输出上限允许显式收紧至 256～4096；没有 tools。四层模板版本、文字和 Schema 均不改写。

费用依据核对于 2026-11-05：[官方价格](https://developers.openai.com/api/docs/pricing)标准价输入 $0.40 / 百万 tokens、输出 $1.60 / 百万 tokens，
[图片计费说明](https://developers.openai.com/api/docs/guides/images-vision#calculating-costs)规定该模型按 32px patches、1.62 倍计费。
预检使用未缩小图片的 patches、文字/Schema UTF-8 bytes 和额外 4096 tokens 余量作保守估算，不扣缓存优惠；
这不是精确分词、供应商强制扣费上限或实际账单，不含未知税费。`--max-cost-usd` 低于该估算则拒绝。
价格核对超过七天拒绝真实执行，需先复核。账户权限、额度及真实可用性未调用探测，仍 UNKNOWN。
用户必须先明确确认具体模型、一次调用及 USD 费用上限；代码开关不能代替该授权。

### 离线预检与后续单次调用

可现在运行，零网络、无需凭证、不迁移数据库、不创建 Run：

```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 \
  backend/.venv/bin/python -m aigc_workbench.c01_cli preflight-real \
  --snapshot-id 8a72fce2-f08d-4a69-8f94-fb3ccc11a664 \
  --model gpt-4.1-mini-2025-04-14
```

输出明确为 `PLANNED MODEL INPUT — OFFLINE PREFLIGHT ONLY`；显示完整文字和图片来源，省略大段 Base64，
`request_sha256` 覆盖未省略的精确请求 JSON。`actual_model_input/response=null`、`model_api_call_attempted=false`。

**以下仅供用户另行批准模型与一次 USD 0.02 上限后执行，本阶段没有执行。**
先按下文配置后端私有 Key，不在命令、源码、日志或 VITE 配置中写入密钥。费用估算保护的限制见上文。
保留以下 operation ID；结果未知时不得换 ID 盲目重试。

```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 AIGC_ENABLE_C01_REAL=1 \
  backend/.venv/bin/python -m aigc_workbench.c01_cli run-real \
  --snapshot-id 8a72fce2-f08d-4a69-8f94-fb3ccc11a664 \
  --model gpt-4.1-mini-2025-04-14 --max-output-tokens 4096 \
  --operation-id 56d3926f-9ab6-4e31-b7cd-63f8043fc0aa \
  --confirm-paid-call --max-cost-usd 0.02
```

真实入口同时需要 development、两个显式开关、付费确认、预算、凭证及有效预检。只调用一次，没有自动重试或 fallback。
同 operation ID 返回原记录，包括 FAILED、OUTCOME_UNKNOWN、RUNNING，不再次调用；更换模型、输入、参数或传输类型产生冲突。

### 结果、Evidence 与失败边界

调用路径：CLI → C01RealService.preflight（复用 S02 Context / Prompt / Snapshot 校验）→ 付费门禁 →
C01ModelStore 固定 Run / Attempt 和计划请求 → 持久化派发意图 → OpenAIResponsesTransport 单次 POST → 原始响应 / 解析 → 终态 Evidence。
代码：[c01_real.py](backend/src/aigc_workbench/c01_real.py)、[c01_model.py](backend/src/aigc_workbench/c01_model.py)、
[c01_model_store.py](backend/src/aigc_workbench/c01_model_store.py)、[c01_cli.py](backend/src/aigc_workbench/c01_cli.py)。

V3→V4 在明确真实执行时追加 `c01_model_runs`：一条记录包含一个 Run、一个 Attempt、固定请求及 Evidence，
独立于 S02 TEST_ONLY 表，不更新 Task/Asset/Slot 或旧 Snapshot。新应用启动或 S02 写入口也会应用最新顺序迁移。
真实执行前应按既有 SQLite 纪律备份；本阶段实际预检库仍为 V3，迁移及回滚在临时数据库验证。
完整请求包含 Base64 图像，属于商业输入，只保存在现有受控本地数据库，不提交 Git。
计划请求、传输实际输入、真实响应分字段保存；派发意图不证明供应商已经收到请求。
若进程在派发前后退出，保留 RUNNING / DISPATCH_INTENT_RECORDED、actual_model_input=null，不能从中推导已发送或未发送。
正常返回时 `actual_model_input` 是交给真实 HTTP 传输的精确请求，不证明供应商内部有效 Prompt 或注意力。

收到 HTTP 响应后保存状态码、可得 request ID、原始响应文本/JSON、耗时、可得 usage；API Key 及 Authorization 头不入 Evidence。
原始响应中若回显 Key 会被脱敏。`provider_cost=null` 表示没有实际账单；usage_based_cost_estimate 只是按可得 usage 和标准价推算。
解析要求响应 completed、模型一致、一个 output_text，严格核对六个 string 数组；拒绝、额外/缺失/重复字段、非法 JSON、未完成和 HTTP 错误均保留证据并失败。
结构化理解标为 AI_DERIVED / NOT HUMAN EVALUATED，不保存成已确认业务事实，不更改业务状态。

超时/连接中断/不完整读取且没有可靠响应，以及 HTTP 408/5xx 时标 `OUTCOME_UNKNOWN`，费用未知，不自动再次付费；60 秒为 socket 超时，不是端到端计时承诺。
终态证据保存失败则报 `MODEL_EVIDENCE_OUTCOME_UNKNOWN`，保留先前固定请求与派发意图，不返回伪成功、不自动重发。
查询真实执行（RUN_UUID 替换为返回的真实 Run 身份，不触发调用）：

```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 \
  backend/.venv/bin/python -m aigc_workbench.c01_cli inspect-model-run --run-id RUN_UUID
```

本阶段离线验证涵盖请求真实编码、四层不变、身份/哈希、门禁、六字段解析、异常、Evidence、幂等不重发及 V3→V4 回滚；
HTTP 行为使用明确假响应，测试记录标 OFFLINE_TRANSPORT_TEST，不冒充真实理解或供应商 Evidence。
原 F01/S01/S02 回归保留，仅最新 Schema 版本断言更新为 4。没有真实供应商请求、账户可用性验证、真实 Token Usage、账单或人工视觉评价。

### P01-01：后端私有凭证与终端输出

私有文件固定为 `backend/.env.local`，当前 `.gitignore` 的 `.env.*` 已覆盖该路径，无需重复规则。
文件须由当前用户拥有、权限为 `0600`；拒绝符号链接、非常规文件、不安全权限、重复 Key、无效格式和过大文件。
由用户在本地编辑器亲自填入一行 `OPENAI_API_KEY=` 的值，可有空行或整行注释，不把 Key 粘贴到聊天、命令参数或日志：

```bash
nano /Users/andy/projects/aigc-workbench/backend/.env.local
```

优先级：已有非空且格式有效的进程 `OPENAI_API_KEY` 优先，保持原值；只有不存在或为空时才从上述文件加载。
已有进程变量格式错误则明确拒绝，不静默换 Key。文件只允许该单项凭证，不执行 shell、变量展开或任意环境配置；
文件不能开启 real execution、改变数据目录或向 Vite 注入凭证。Settings、普通 API 启动和 S02 测试执行不自动加载此文件。
配置状态命令不连接网络、不访问业务数据库，只输出 KEY_CONFIGURED / KEY_MISSING（配置本身不安全时输出不含凭证的明确错误）：

```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 \
  backend/.venv/bin/python -m aigc_workbench.c01_cli credential-status
```

KEY_CONFIGURED 仅证明进程取得非空配置，不代表凭证通过供应商认证、账户有额度或指定模型可执行。
缺失时停止，不调用模型。已有文件不覆盖、不开备份，不提交私有文件；不要使用 `source` 或 `export $(cat ...)` 来加载。

run-real / inspect-model-run 的终端仅输出身份、状态、耗时、usage 及费用估算摘要；不输出请求、Base64 图片、原始响应或模型正文。
完整材料保留在本地数据库 Evidence，后续授权的真实实验可导出至被忽略的私有 `.runtime` 文件供人工审核。
preflight-real 仍可展示四层原文和图片来源，但省略 Base64。日志不记录 Authorization 头或 Key 任何部分。

2026-11-05 核对：[官方弃用公告](https://developers.openai.com/api/docs/deprecations#2026-10-14-gpt-4-series-model-deprecations)
将 `gpt-4.1-mini-2025-04-14` 标为 Deprecated，关闭日期为 2027-01-14，官方替代候选为 `gpt-6-luna`。
本实验保持固定快照，不自动切换；模型元数据 GET 可访问也不等于 Responses、图片及 Structured Outputs 推理已验证。
真实 C01 仍须用户明确确认固定模型、一次调用及预估最高 USD 0.02；估算守卫不保证供应商最终账单的绝对上限。

### P01-03：代理感知的正式模型传输

原 `http.client.HTTPSConnection` 不读取代理环境变量，导致本机绕过既有 HTTPS 代理并在直连 TCP 阶段超时。
现在 `OpenAIResponsesTransport.send()` 与 `check_model_access()` 共用 `_request()`，使用 urllib 的同一 HTTPS 客户端、TLS 默认验证和 60 秒 socket 超时。
四层 Prompt、原始请求 JSON bytes、图片、输出 Schema、Snapshot 和 Run/Evidence 语义均不改变。

- 使用进程的 `HTTPS_PROXY`，否则使用 `ALL_PROXY`；同名小写变量按标准库规则优先。HTTPS 目标不使用单独的 HTTP_PROXY。
- 遵守 `NO_PROXY` / `no_proxy`，匹配时明确直连。没有 HTTPS/ALL 代理时也明确直连；不隐式读取系统代理设置。
- 当前最小实现支持 `http://` 代理上的 HTTPS CONNECT，含必要代理认证；认证头仅交给 CONNECT，不转发给 API。
- 不支持 `https://` 代理端点的双层 TLS 或 SOCKS URL。若它们是选中的唯一配置则明确拒绝，不降级、不静默转直连；现有 `HTTPS_PROXY=http://...` 路径可直接复用。
- 代理连接失败、超时、TLS 验证失败使用不含凭证或代理 URL 的固定错误；不增加代理故障后的直连尝试。
- API 目标固定为官方域名，只允许既有 Responses POST 和指定模型元数据 GET；禁用所有重定向，含同域重定向。
- 保留 HTTP 错误状态及脱敏原始响应，交给现有 Evidence 保存；网络结果未知仍由原逻辑记 OUTCOME_UNKNOWN。没有自动 POST 重试或认证重试。
- 不输出 Key、代理用户名/密码、Proxy-Authorization 或 Base64 图片；响应回显凭证时脱敏。

无付费模型访问检查（不要求开启真实执行、不访问业务数据库、不创建 Run）：

```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 \
  backend/.venv/bin/python -m aigc_workbench.c01_cli check-model-access \
  --model gpt-4.1-mini-2025-04-14
```

每次显式命令仅一次 GET；输出实际 HTTP 状态、所选路由类别、耗时及脱敏错误。只有 HTTP 200 且模型 ID 匹配才标 MODEL_METADATA_ACCESSIBLE。
此结果不等于 Responses POST、图像理解、Token 额度或 C01 AI EVALUATED 已验证。
原 preflight-real / run-real 命令及付费授权要求保持不变；本轮不执行 Responses POST。

### C02：Quick-A 当前绑定参考的真实理解

C02 从真实 Task / Slot 的当前参考绑定选择 `marketing_reference`，验证归属、角色、原始字节、SHA-256 及准备/执行时绑定一致性。
输入只包含这一张参考图；商品图、C01 输出及初步评价、历史人工答案、任务名和图位制作要求不进入本次参考观察 Prompt。
Quick-A 保持同款同配置运营前提；参考宣称按原图记录，不独立认证为实售商品事实，也不以参考商品外观覆盖实际商品图。

[c02_reference.py](backend/src/aigc_workbench/c02_reference.py) 只增加 C02 的来源选择、有效输入、四层 Prompt、八类结构化输出和校验。
执行直接复用 `C01RealService.run()` 的单次请求、开发/付费门禁、未知结果处理及幂等语义，复用同一代理客户端和 Evidence。
物理 SQLite 表及固定图片目录沿用历史 `c01_*` 名称，不新建 Runtime；C02 JSON 明确使用 reference_asset_id、slot_id 和 C02 标签，不把参考伪装成 product_image。旧 C01 Snapshot 和结果不改写。
输出分为布局层次、参考商品/特写、可见文字、营销宣称、图文符号关系、参数/比较、设计风格、不确定项。输出为待核对模型观察，不写回商品事实或 Pass。

受控开发入口（UUID 替换为真实对象；prepare/preflight 不调用模型）：

```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 backend/.venv/bin/python -m aigc_workbench.c01_cli prepare-c02 --task-id TASK_UUID --slot-id SLOT_UUID --reference-asset-id REFERENCE_UUID
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 backend/.venv/bin/python -m aigc_workbench.c01_cli preflight-c02 --snapshot-id SNAPSHOT_UUID --model gpt-4.1-mini-2025-04-14
```

仅在取得指定模型、一次调用及预算明确授权后使用真实命令；同 Operation ID 不重新付费执行，未知结果不换 ID 盲目重试：

```bash
env AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 AIGC_ENABLE_C01_REAL=1 backend/.venv/bin/python -m aigc_workbench.c01_cli run-c02 --snapshot-id SNAPSHOT_UUID --model gpt-4.1-mini-2025-04-14 --max-output-tokens 4096 --operation-id OPERATION_UUID --confirm-paid-call --max-cost-usd 0.02
```

真实请求、原始响应、结构化结果和复核材料保存在被忽略的 `.runtime/c02-real/` 及运行库，不提交商业图片或凭证。
C01 初步评价仅作待人工复核的质量记录；后续不得仅凭其不完整组件列表决定保留结构，须回查原始商品图及有效依据。

### 当前主图：一次 C03 / C04 / C05 双图判断

[quick_a_creation_decision.py](backend/src/aigc_workbench/quick_a_creation_decision.py) 仅为当前 Quick-A 主图准备来源对齐和制作决定。
两张原图按 product_image、marketing_reference 的顺序实际进入同一次多模态请求；C01/C02 原始理解及初评以 AI-derived / 待复核来源身份进入 Context，不替代原图。
当前绑定、主图尺寸、保存的逐图要求、上游 Run/Snapshot/素材哈希在准备及调用前再次核对；旧记录不覆盖。

仍复用已有单次 Runtime、客户端、代理、凭证加载、Snapshot 和 Evidence；不新建 Agent、Workflow、Prompt Registry 或业务对象。
本次分段输出为 01_SOURCE_ALIGNMENT、02_IMAGE_CREATION_DECISION；后者包含 C04、C05 及完整 C06 制作输入，状态保持候选、待审核，不自动生成图像或更新 Pass。
参数守卫保持固定模型；本次两图均计入费用估算，可显式设置 8192 输出 tokens 和 USD 0.05 估算上限。C01/C02 的默认 4096 上限不变。

入口为 prepare-creation-decision（显式 Task/Slot/两张 Asset/两个上游 Run/两份初评）、preflight-creation-decision、run-creation-decision。
真实命令仍须 development、两个既有显式开关、付费确认、一次操作身份及预算；没有自动重试或模型切换。
运行产物保存在被忽略的 `.runtime/creation-decision/`，两个可读成果及 C06 输入由真实模型结果导出，不能以人工补写冒充模型输出。

### C06：当前主图的一次真实候选

[c06_generation.py](backend/src/aigc_workbench/c06_generation.py) 复用既有单次执行、代理感知 HTTPS、凭证与 Evidence。
固定 `gpt-image-2.6-sunburst`，调用 `/v1/images/edits`；两张真实原图按商品、营销参考顺序以 JSON Data URL 发送。
参数为 `1024x1536`、`quality=high`、`complexity=high`、`n=1`、`stream=false`、`output_format=png`，不设置 `input_fidelity`。
完整制作指令来自已归档的 C06 交接文件，原始模型决定和助手质量修订分别记录路径、哈希与权威状态。

开发入口：`backend/.venv/bin/python -m aigc_workbench.c06_cli`，提供 `prepare`、`preflight`、`run`、`inspect-candidate`。
真实 `run` 还需要既有 development/C01 显式开关、`AIGC_ENABLE_C06_REAL=1`、指定模型、全新 Operation ID、`--confirm-paid-call` 和 `--max-cost-usd 1.00`。
预算是本地估算守卫：官方输出计算器与当前费率，加上明确标注的本地输入规划余量，不是供应商账单上限。
无自动重试、模型切换、第二张生成或运营 Pass；超时保留 `OUTCOME_UNKNOWN`，同一操作不会再次发送付费请求。

SQLite V5 仅增加不可变 `image_versions` 记录，候选 PNG 保存在 `.runtime/f01/image_versions/`，关联真实 Task/Slot/Run/来源，状态固定为 `CANDIDATE_REVIEW_PENDING`。
若图片本地保存失败，先查已有 Run，使用同一操作恢复本地保存，不能换 Operation ID 重新付费。开发入口默认关闭；内部 Pilot 的直接操作与独立运行方式见下文。
请求、原始响应及对照评价材料位于被忽略的 `.runtime/c06-real/`，不提交图片、运行数据或凭证。


### Quick-A 内部 Pilot：直接操作、审核与交付

沿用 Workspace OUTPUT 区及 [generation_application.py](backend/src/aigc_workbench/generation_application.py)，不创建第二个工作台或 Runtime。
开发启动显式设置 `AIGC_ENV=development AIGC_ENABLE_C01_TESTING=1 AIGC_ENABLE_C01_REAL=1 AIGC_ENABLE_C06_REAL=1 AIGC_ENABLE_C07_REAL=1 AIGC_ENABLE_WORKSPACE_GENERATION=1`，再使用原 uvicorn 命令。默认启动不开放真实生成或修改。

- 先在历史候选中查看版本，再明确选择“以当前查看版本为修改基础”。预览切换不会串用修改草稿；草稿按图位与基础版本保留在当前页面内存中，刷新或离开页面前须自行保留未提交文字。
- [c07_revision.py](backend/src/aigc_workbench/c07_revision.py) 使用四层 Prompt、选定基础 PNG、实际商品及当前参考三张图片。只带当前保存的约束及本轮意见，保留基础图已实现的有效设计，不重放历史修改文字，不重新执行 C01～C06。
- 运营手动点击生成或修改直接提交业务命令，不再弹出费用确认。`pilot` 模式不设单次、每日或用户费用/次数上限；规划估算、Usage 与用量费用仍保存。仍复用单线程持久派发、Operation ID 和真实 Run/Evidence，不重试、不切换模型。页面查询、刷新及后端重启不重新发送请求；结果未知先查证。原开发 CLI 的预算协议与此分开。
- 新 PNG 保存为独立 Image Version，关联基础版本和本轮意见；旧图片、Snapshot、原始模型响应和历史制作决定不可变。失败或新候选不会替换当前有效 Pass。
- [image_review.py](backend/src/aigc_workbench/image_review.py) 的人工审核是独立追加记录，保存版本、图片 SHA、审核人、时间和反馈。审核人仅是未经身份验证的操作者标签，无登录、角色或用户权限系统，仅允许明确可信的内部局域网访问。模型不能自动 Pass。
- SQLite V7 在 V6 上追加 `image_reviews` 与 `slot_deliveries`；旧执行和版本行不改写。只有显式人工 Pass 更新对应图位的有效交付；Reject 保留候选与历史。已有 Pass 的撤销不属于本轮功能。
- 下载接口只读取当前合法 Pass 的 PNG，核对业务归属和文件哈希；携带旧审核身份的下载链接返回冲突。历史 PNG 仍可预览，但不等于当前有效交付。审核使用操作幂等与当前交付身份校验，避免多页签静默覆盖。
- 仅测试注入的替代工具产生 `TEST_SUBSTITUTE` / `TEST_REVIEW`，不证明真实模型质量或业务接受；生产读取拒绝将测试审核当作真实 Pass。

运行数据、商业图片和完整请求/响应仅保留在已忽略的 `.runtime` 及 SQLite 中，不提交 Git。真实生成成功不等于商品结构正确、运营批准或 Pass。

Pilot 生产构建、Mac mini 环境核查、私有配置、launchd、备份/恢复、更新与回滚见 [部署准备说明](deploy/README.md)。本轮仅准备，未安装或部署。上海使用与供应商支持地区不符，尚不能发布真实 OpenAI Pilot。P2-B1 已接通新 Task 的按需理解和制作决定；缺少有效决定时从当前素材重新准备，不伪造制作输入。


### Quick-A 新建资料与当前理解模型

新建页面可同时选择任务名、商品图、营销参考、首个图位、输出像素尺寸及本图要求；一次点击后依次调用现有 Task / Asset / Slot API，成功后进入正式 Workspace。保存本身不调用模型。每步保留服务器身份及 Operation ID；部分失败继续同一次保存，已保存文件跳过。待上传文件暂存在当前浏览器 IndexedDB，成功后清除；浏览器不支持暂存时会提示刷新需重选原文件。服务器始终是业务状态权威。

整体运营要求可在新建页与 Task 原子保存，也可在 Workspace 编辑；`PUT /api/tasks/{id}/overall-requirement` 接收 `text / expected_version / operation_id`。作用范围固定为本 Task 全部图位的后续生成与显式修改，最多 2000 字符。未提供为 V0，后续保存追加 V1、V2…；清空也保留新版本。并发旧版本返回 409，同一 Operation ID 不重复追加；未保存输入不会冒充服务器状态。

SQLite V9 只追加不可变 `task_requirements` 和创建操作的原始整体要求字段，既有任务保持 V0，历史 Snapshot/Run/图片/Pass 不改写。C01/C02 保持纯图片观察；C03～C05 通过 Effective State 与 Context Assembly 同时取得整体和逐图要求及其来源/范围/版本。C06 冻结并发送适用要求，整体要求更新后不能重用失效制作决定；C07 使用明确选定基础版本、当前要求和本轮意见，旧修改报价在要求变化后以 409 拒绝。保存整体要求不派发任务、不重做图片、不使原 Pass 失效。自然语言冲突不采用简单最后输入优先，也不新增全局审批步骤。

新 Workspace 操作的 C01、C02、C03/C04/C05 默认使用 `gpt-6.1-terra`（[模型](https://developers.openai.com/api/docs/models/gpt-6.1-terra)、[迁移参数](https://developers.openai.com/api/docs/guides/latest-model)）：实际图片 data URI、四层 Prompt、严格 JSON Schema 保留，`reasoning.effort=medium`，不传 `temperature`；C01/C02 输出上限 8192、合并制作决定 16384（均包含 reasoning tokens）。解析跳过 reasoning 项，仅解析最终 message 的结构化文本；拒绝、不完整响应和模型不一致均保留原始证据。固定 S03 历史 CLI 与既有 Snapshot/Run 不改写。

C06/C07 继续使用 `gpt-image-2.6-sunburst` 与原 Images Edits 接口。开发与 Pilot 服务端启用开关保持独立；更新默认模型不会启用被禁用的真实 AI。

按[官方价格](https://developers.openai.com/api/docs/pricing)与[缓存计费](https://developers.openai.com/api/docs/guides/prompt-caching)，费用估算分别计算普通输入、缓存读取、30m/12h 缓存写入及含 reasoning 的输出；客户端未设置 TTL 时沿用官方默认 30m，返回未拆分写入明细时明确记录该假设。预检图像 token 使用保守启发式，非官方精确分词；估算与实际供应商账单分开，禁止隐式付费重试。

### P2-B1：新 Quick-A 任务直接制作

运营在原 Workspace 创建任务、上传商品与参考、绑定图位、选择输出像素尺寸及可选要求后，点击“生成一张候选”即可启动；不需要开发者预先运行 C01～C05 CLI，也不增加完整 Listing、Planning 人工预审或费用审批弹窗。

实际调用路径：`GenerationWorkspace / useSlotGeneration` → `POST generations` → `GenerationApplication.submit` → 既有 `C06Dispatcher` → `QuickACreation.execute` → C01 / C02（按来源条件复用）→ C03/C04/C05 合并制作决定 → C06 → 原 `CandidateStore`。有效制作决定仍可直接复用；变化的来源或要求会使旧依据失效，不能用失效结果继续制作。

- 当前输入使用最新上传的一张商品图，界面明确显示其来源；绑定的参考仅提供 Quick-A 营销与设计依据。早先素材不删除，本轮不做多商品照片融合。
- C01 复用四层 Prompt，支持已校验 JPEG / PNG / WebP，不再要求新任务匹配历史实验 UUID。固定 S03 CLI 的原实验身份校验仍保留。
- C02 仍是纯参考观察；C03～C05 使用两张原图、理解结果及本图位有效要求，不带入历史样本颜色、电压或固定 mc1～mc4，也不补造人工初评。
- C06 直接接收两张原图字节。执行交接由已保存的当前制作决定派生于 `creation_decisions/<snapshot UUID>/`，原始模型结果与派生指令分别保留。
- SQLite V8 只在既有 `c06_dispatches` 追加 `progress_json`；操作先持久化，阶段的 Snapshot / Run / Attempt 与派生 Operation ID 可追溯。模型请求、原始响应、Usage、费用估算仍在原 `c01_model_runs`。未知结果及进程中断不自动恢复付费执行。
- 1536×1536 沿用 1024×1024 原生方图 + Pillow LANCZOS 等比例放大，原生和最终 PNG、尺寸及处理方式分别留证，候选关联最终文件。C07 保留选定基础版本及目标像素尺寸，不重放历史修改意见。
- 正常启动仍默认关闭模型执行。已有服务器私有 Key、代理及 Workspace/C01/C06/C07 开关继续使用；本轮没有付费调用、没有部署。工程测试通过不等于真实 AI 质量或运营接受。

### Quick-A V1：当前工作台执行方式

- 默认首次生图为：当前 Task 最新商品图 + 当前 Slot 绑定参考图 + 当前有效整体/单图要求 → 冻结独立 Context、Prompt 和 Snapshot → 一次 C06 图片调用 → 保存待审候选。核心顺序见 `backend/src/aigc_workbench/quick_a_direct.py`；不经过 C01～C05，也不拼接其他图位或聊天历史。
- 尺寸图与其他图位使用相同入口，不要求额外白底图或尺寸核对流程。Prompt 要求保留参考已有的尺寸数值、单位、文字和标注关系，不编造新尺寸；真实成图仍需运营人工审核。
- 工作台可一键生成全部有效图位，或勾选多个图位生成。数量 N 来自当前任务；`AIGC_GENERATION_MAX_WORKERS=3` 控制后台同时执行数 M（支持 1～16），其余排队，N 不受 M 限制。当前 Dispatcher 是单进程服务，不要为同一数据目录启动多个 Uvicorn worker。
- 每个 Slot 独立保存 Operation、Snapshot、Run、Attempt 与候选版本；单图失败不撤回其他结果。重复 Operation 保持幂等，结果未知时不自动重试。图片模型等待期间不持有 SQLite 写事务。
- Slot 保存携带读取时的 `expected_version`，后端原子更新，过期页面返回 HTTP 409；整体要求与 Slot 要求更新仅影响后续执行，不改写旧 Snapshot 或已有 Pass。
- C07 使用运营选定的基础版本、其关联商品、当前 Slot 参考及当前有效要求，不重放历史修改文字。新候选需要人工 Pass，不覆盖已有有效交付。C01～C05 和旧版本读取保留兼容。
- 首页任务菜单支持持久重命名和二次确认逻辑删除；排队、执行中或结果未知的任务不能删除。逻辑删除保留素材、版本和执行证据。
- 已启用模型的工作台中，主动提交即生成操作，不再增加费用确认、价格过期复核或人工预算上限；保留实际 Usage、耗时、错误与认证/限流约束。开发者独立 CLI 的显式授权规则另见下文。
- 本地终端逐步日志使用 `AIGC_ENV=development AIGC_DEBUG_QUICK_A=1`；只输出脱敏的八步执行信息，不打印图片 Base64 或凭证。模型执行开关默认仍为关闭，详见现有启动说明。
- 必要离线回归：`backend/.venv/bin/python -m pytest backend/tests -rxX`、`npm --prefix frontend test`、`npm --prefix frontend run build`。测试全部使用 Fake/Mock；既有 XFAIL 表示 `TaskService.get_draft` 仍加载兄弟 Slot 元数据，未实现严格不读取，但实际模型输入隔离由专项断言验证。离线通过不代表真实成图质量验收。

### Quick-B V1：不同商品参考的换主体生图

首页点击“新建 Quick-B”，或在新建页选择 Quick-B；上传实售商品图、各图位参考及可选要求，保存后进入同一个工作台。可逐图生成、勾选多图或生成全部有效图位；生成数量与 `AIGC_GENERATION_MAX_WORKERS` 的并发上限独立。

业务实现独立位于 `backend/src/aigc_workbench/quickb.py`：`read_inputs → build_prompt → prepare → preflight → run`。一次 C06 图片调用完整替换参考中的商品主体、特写、配件和场景商品细节，保留运营默认认可的适用营销内容；明确重要参数冲突只定点处理，不增加普遍人工确认门禁。Quick-A 核心实现保持独立，没有统一 `quick_direct.py` 或 C01～C05 调用。

C07 继续使用选定基础版本的商品来源、当前 Slot 参考及有效要求，并保留 Quick-B 换主体规则，避免修改时重新混入参考商品。候选、人工 Pass/Reject、任务菜单、要求版本与 Slot 并发保护均复用已有机制。数据库 V13 仅扩展原 Task 的模式约束，迁移失败事务回滚；旧 Quick-A 行、关系和执行证据保留。

专项离线测试见 `backend/tests/test_quick_b.py` 和 `frontend/src/test/quickB.test.tsx`。Fake/Mock 能验证请求、来源、并发和保存行为，实际主体替换完整性、文字/规格保真与冲突处理质量仍需真实模型和运营验收。

### Listing V1：商品资料制图

首页“新建 Listing”，上传实际商品图，直接粘贴必需的 Listing 原文，并可补充商品描述、卖点和整体要求。文本框默认五行，可滚动、展开和再次编辑；无需 JSON。新建页可勾选固定图位，默认启用主图，卖点和场景图按需增加。每个图位设计参考可选，没有参考也可以生成。

独立业务核心为 `backend/src/aigc_workbench/listing.py`：`read_inputs → build_prompt → prepare → preflight → run`。商品图控制外观，Listing/补充资料控制商品营销内容，设计参考仅辅助布局与风格；每次一次 C06，不执行 C01～C05。版本化原文保存在 Task 关联的 `task_listing_materials`（V14 迁移），按 Task 共享、Slot 隔离；保存携带资料版本和 Operation，旧页面返回 409，重放不重复增加版本。修改资料不改写旧 Snapshot、候选或 Pass。

C07 继承选定基础版本及其商品、当前有效商品资料、本 Slot 可选参考与本轮修改。支持参考的增删，不重放历史意见；批量生成、Worker 上限、错误处理、版本和审核沿用现有实现。开发环境会记录脱敏的 Listing 身份、耗时和结果，详细请求及来源保存在既有私有 Evidence 中。

尺寸图遵循已确认的专用真实标注素材规则，不从设计参考或输出像素推断尺寸。重大商品矛盾、必保留信息缺失和尺寸依据不足的处理要求写入 Prompt；不宣称已实现独立的事实冲突识别器或新审批流程，实际模型遵守情况仍需人工验收。专项 Fake 测试见 `backend/tests/test_listing.py` 与 `frontend/src/test/listing.test.tsx`。

### C01 本地终端调试：保留的独立/历史步骤

`AIGC_ENV=development AIGC_DEBUG_C01=1` 时，C01 在终端 stderr 打印
`INPUT / PROMPT / RESPONSE / PARSED RESULT / DECISION / NEXT STEP INPUT`，每条带 Task、Image Slot（调用方可提供时）、Step、Run、Attempt 和 Transport。
Prompt 来自实际请求中的文字；图片只显示 Asset、哈希、角色和本地路径，图片编码及凭证被隐藏。
没有显式开启、非 development 环境及其他能力均不输出这些详细日志。不会改写原始请求、响应或已保存 Evidence。

Quick-A 默认“生成一张候选”已使用上述直生图入口；`AIGC_DEBUG_C01` 只控制保留的独立 C01 或旧多阶段链路日志，不会改变或暂停模型执行。
若本轮只手动观察 C01，先在原工作台保存商品、参考及图位，取得 Task UUID 和该任务 `slots[].id`，再在仓库根目录手动运行下列命令。
该命令使用 Workspace 同一个 `ProductUnderstandingService`，只发起一次 C01；不会调用 C02、制作决定或生图。
真实调用使用已有后端私有凭证配置及预算门禁；命令重新运行是新的操作，可能产生新费用。

```bash
cd /path/to/aigc-workbench
AIGC_ENV=development AIGC_DEBUG_C01=1 \
AIGC_ENABLE_C01_TESTING=1 AIGC_ENABLE_C01_REAL=1 \
DEBUG_TASK_ID='替换为 Task UUID' DEBUG_SLOT_ID='替换为 Image Slot UUID' \
DEBUG_C01_MAX_COST_USD='1.00' backend/.venv/bin/python -B - <<'PY'
import os
from uuid import UUID, uuid4
from aigc_workbench.application import TaskService
from aigc_workbench.c01_execution import C01Service
from aigc_workbench.c01_model import DEFAULT_UNDERSTANDING_MODEL, VISION_OUTPUT_TOKENS
from aigc_workbench.c01_persistence import C01EvidenceStore
from aigc_workbench.config import Settings, load_openai_credentials
from aigc_workbench.file_storage import LocalImageFiles
from aigc_workbench.persistence import SQLiteTaskStore
from aigc_workbench.quick_a_sources import ProductUnderstandingService, current_sources

task_id, slot_id = UUID(os.environ['DEBUG_TASK_ID']), UUID(os.environ['DEBUG_SLOT_ID'])
settings = Settings.from_environment()
if not settings.database_path.is_file():
    raise SystemExit('需要已有本地业务数据库；未创建任务或调用模型。')
if load_openai_credentials() != 'KEY_CONFIGURED':
    raise SystemExit('后端私有凭证未配置；未调用模型。')
database = SQLiteTaskStore(settings.database_path)
tasks = TaskService(database, LocalImageFiles(settings.data_dir))
base = C01Service(settings, tasks, C01EvidenceStore(database, settings.data_dir))
state, _ = current_sources(base, task_id, slot_id)
snapshot = base.prepare(task_id, UUID(state['product_asset_id']))
c01 = ProductUnderstandingService(settings, base)
c01.debug_slot_id = slot_id
result = c01.run(UUID(snapshot['id']), DEFAULT_UNDERSTANDING_MODEL, uuid4(),
    confirmed=True, max_cost_usd=os.environ['DEBUG_C01_MAX_COST_USD'],
    max_output_tokens=VISION_OUTPUT_TOKENS)
print('C01 finished:', result['status'], 'Run:', result['run_id'], '; no later stages invoked.')
raise SystemExit(0 if result['status'] == 'SUCCEEDED' else 1)
PY
```

`NEXT STEP INPUT` 是 C01 返回调用方的 Run 身份和已解析结果。原链路的 C02 只理解参考，不消费 C01 结果；后面的合并 C03/C04/C05 才通过 `c01_run_id` 读取它。
缓存复用仅打印 `REUSE_SAVED_RUN_NO_MODEL_CALL` 和返回边界，不把旧响应冒充新调用。
如需仅准备资料，可用原启动方式保持 Workspace 生成关闭：
`AIGC_ENV=development AIGC_ENABLE_WORKSPACE_GENERATION=0 backend/.venv/bin/python -m uvicorn aigc_workbench.app:create_app --factory --host 127.0.0.1 --port 8022`，前端仍为 `npm --prefix frontend run dev`。

隔离浏览器测试（仅开发机、已有依赖；全程 TEST_ONLY）：

```bash
backend/.venv/bin/python backend/tests/run_quick_a_test_server.py --data-dir .runtime/p2-b1-test-only --port 8033
```

另一个终端：

```bash
cd frontend
AIGC_API_TARGET=http://127.0.0.1:8033 node_modules/.bin/vite --host 127.0.0.1 --port 8034 --strictPort
```

访问 `http://127.0.0.1:8034`，上传测试目录中的 `product.png`、`reference.png`。该测试入口显式注入 OFFLINE 适配器、使用无效测试凭证并禁止真实供应商传输；拒绝使用既有未标记目录，不读取 `backend/.env.local`，不作为部署入口。测试候选、审核和下载均标记 TEST_ONLY，正常运营服务拒绝将其审核为真实交付。

回归：`backend/.venv/bin/python -m pytest backend/tests`、`npm --prefix frontend test`、`npm --prefix frontend run build`。测试、截图、数据库和生成文件仅保留在忽略的运行目录，不提交 Git。
