# AIGC Workbench

React 工作台 + FastAPI 模块化单体 + SQLite / 本地原始素材文件。
当前完成 Quick-A Task 的创建、真实商品/营销参考上传、最小图位配置及恢复。

- **S01-A ENGINEERING VERIFIED**：Task 演进、V1→V2、Asset/Slot、幂等写入与 R01～R04。
- **S01-B ENGINEERING VERIFIED**：范围限于本轮实际接通并在桌面浏览器验证的素材上传、图位参考绑定、保存/刷新/重新打开/进程重启恢复。
- 这不是 BUSINESS ACCEPTED；完整旧 UI、带数据候选、历史版本、修改、Pass / Reject、Inspector 仍未完整视觉验收。
- **S02 ENGINEERING VERIFIED**：受控 CLI 完成 C01 输入准备、四层 Prompt、快照及测试证据；没有真实 AI 理解或 S03。
- 不支持真实 AI、生图、图片修改、审核或完整三模式。
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

[.env.example](.env.example) 只有示例，不含凭据。后端读取进程环境，不自动加载环境文件。

| 配置 | 默认值 / 用途 |
|---|---|
| `AIGC_DATA_DIR` | `.runtime/f01`，相对仓库根目录解析；沿用 F01 数据目录 |
| `AIGC_ALLOWED_ORIGINS` | 本机 8021 的 Origin，逗号分隔 |
| `AIGC_API_TARGET` | `http://127.0.0.1:8022`，Vite 服务端代理 |
| `AIGC_ENV` | `development`；C01 CLI 仅允许该环境 |
| `AIGC_ENABLE_C01_TESTING` | `0`；显式为 `1` 才能使用 C01 开发 CLI |

目录内包含 `workbench.sqlite3`、`staging/<UUID>.part`、`assets/<UUID>`。
数据库、原始商业素材、备份、验证记录、环境文件、虚拟环境、依赖、缓存及构建产物不纳入 Git。
没有模型客户端，不需要任何模型凭据，不得将密钥放入 `VITE_*`。

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

启动时显式初始化/迁移：F01/S01 的 V1→V2 新增 task_creations、assets、image_slots；S02 最新版本 V3 追加 C01 快照与执行证据表，不重建或改写原任务表。
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
**C01 AI EVALUATED：未完成；BUSINESS ACCEPTED：未建立。** 没有真实模型、SDK 请求序列化、生成或 S03 开发。

采用本地 CLI，不新增开发 HTTP API、工作台页面或完整 Inspector。
入口在任何存储访问前检查 `AIGC_ENV=development` 且 `AIGC_ENABLE_C01_TESTING=1`；默认关闭。生产环境即使开关为 1 也拒绝。
`.env.example` 不自动加载，以下环境值仅作用于该命令。没有模型凭据或真实模型开关。

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

S03 仍需单独授权模型、预算及真实调用，验证图片序列化、模型输出契约、视觉理解质量、遵循边界和人工评价；
S02 不能证明模型会关注图片、遵守 Prompt 或产生正确商品理解。
