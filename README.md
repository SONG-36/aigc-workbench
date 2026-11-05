# AIGC Workbench

React 工作台 + FastAPI 模块化单体 + SQLite / 本地原始素材文件。
当前完成 Quick-A Task 的创建、真实商品/营销参考上传、最小图位配置及恢复。

- **S01-A ENGINEERING VERIFIED**：Task 演进、V1→V2、Asset/Slot、幂等写入与 R01～R04。
- **S01-B ENGINEERING VERIFIED**：范围限于本轮实际接通并在桌面浏览器验证的素材上传、图位参考绑定、保存/刷新/重新打开/进程重启恢复。
- 这不是 BUSINESS ACCEPTED；完整旧 UI、带数据候选、历史版本、修改、Pass / Reject、Inspector 仍未完整视觉验收。
- **S02 ENGINEERING VERIFIED**：受控 CLI 完成 C01 输入准备、四层 Prompt、快照及测试证据。
- **S03 第一阶段 IMPLEMENTED / OFFLINE VERIFIED**：真实 C01 接口与预检已准备，默认关闭；尚未调用真实模型，NOT AI EVALUATED。
- 不支持工作台 AI 执行、生图、图片修改、审核或完整三模式。
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
本轮只保存准备资料，不认证商品事实、营销宣称或参考适用性。

`status=draft` 不因上传而变成制作完成。每个 Slot 的 `inputs_ready` 由服务器根据该 Task 的商品素材和已保存营销参考绑定派生。
`materials_ready` 表示至少一个独立图位基础图片输入齐备，不表示所有图位齐备；未绑定图位不阻断其他图位。
`can_generate` 始终为 false，图片执行尚未实现，与素材准备状态分开。

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
