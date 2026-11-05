# Quick-A 内部 Pilot：P2-A 准备记录与 P2-B 部署步骤

状态：P2-A 工程准备；本轮不安装、不复制到服务器、不启动远程服务、不新增付费调用。
核查日期：2026-11-05（Asia/Shanghai）。仓库基线 ec6675b3ebca9bd450856783cf6ff5bde5d85b9a。

## 已确认的运行范围

无需登录、注册、密码、角色或用户级资源权限；没有单次、每日、每用户费用或次数限制，也没有额外付费确认。
点击“生成一张候选”或“修改这张图片”就是对应业务命令。来源完整性、有效制作决定、明确基础版本、Pass 保护仍须满足。
费用预估与实际 Usage 继续记录；它们是观测，不是上限或账单。
原开发 CLI 的显式实验预算协议仍限开发入口使用，不属于 Pilot 运营路径或发布门禁。
审核人记录为未经身份验证的操作者标签，不能声称真实身份审计。

用户明确确认信任整个实际局域网：192.168.112.0/24。其余网段不自动信任。
应用同时检查 TCP 对端、Host 和同源写入；不信任 X-Forwarded-* / Forwarded，不接入公网反向代理或端口映射。
Pilot 关闭 /docs、/redoc、/openapi.json；仅提供业务 API、生产 assets 和明确的 React 路由。
不挂载仓库、运行目录、SQLite、完整原始 Evidence、凭证或开发者 CLI 为 HTTP 资源。

## Mac mini 实际只读观察

| 项目 | 当前结果 |
|---|---|
| SSH | andy_server@192.168.112.37 可连通 |
| 硬件 | Apple M4，10 CPU，16 GiB 内存，arm64 |
| macOS | 26.4.1 / 25E253 |
| Data 卷可用空间 | 约 87 GiB |
| 系统默认 Python | /usr/bin/python3，3.9.6，不满足项目 >=3.12 |
| 已有 Python | /opt/homebrew/opt/python@3.12/bin/python3.12，3.12.13，可用于新 venv |
| 已有 Node | /opt/homebrew/opt/node/bin/node，26.4.0；另有 nvm 24.18.0；非交互 SSH 默认 PATH 未包含 Node |
| Git | 2.50.1 Apple Git |
| 目录 | ~/apps 可写；拟用 ~/apps/aigc-workbench-pilot 当前不存在 |
| 不可用目录 | ~/projects 为指向 /Volumes/server-data/projects 的悬空链接，挂载卷当前不存在，不使用 |
| 子网 | en0 192.168.112.37，netmask 255.255.255.0；MacBook 192.168.112.3 与其直连 |
| 现有服务 | legacy commerce-image-pilot 在 8444；其他服务含 3100、10000、8081/8082/8083、8000、8501、18080、18787、3306、6379 |
| 网络基础设施 | 现有本机代理及 cloudflared 进程已存在；本轮未改动、未将新服务接入 |
| 计划端口 | TCP 8030 核查时未监听；P2-B 启动前必须再查 |
| 防火墙 | macOS 应用防火墙关闭；无无密码 sudo，PF 规则未能读取；不能宣称系统防火墙规则已验证 |
| 常驻条件 | 已有用户 LaunchAgent 服务；AC sleep=0，autorestart=1。新服务计划使用独立 LaunchAgent；开机后需该用户会话存在 |
| 私有配置 | 新 Pilot 配置及 Key 文件不存在；SSH 进程 OPENAI_API_KEY 和代理环境变量均未设置；未读取其他项目凭证 |
| API HTTPS | 无凭证直连在 TLS 前连接被重置；既有本机代理的无凭证探测取得 HTTP 401，仅证明 HTTP 可达 |
| 账户模型权限 | 未做认证 GET，不能证明服务器 Key 或具体模型可用；零推理调用 |

用户确认实际部署地和运营使用地均为中国大陆上海。
[OpenAI 当前支持地区](https://developers.openai.com/api/docs/supported-countries)不包含中国大陆，并说明在列表外访问或提供访问可能导致账户受限。
因此真实 OpenAI Pilot 发布当前为 REGION_UNSUPPORTED；代理出口连通不能将该结论改为符合要求。
本轮不更换供应商、模型或端点，也不提供绕过地区条件的部署操作。

## 独立服务布局与启动

以下是 P2-B 的操作说明，本轮未在 Mac mini 执行。只有地区/供应商条件已解决后，才能进入真实模型部署。

```text
/Users/andy_server/apps/aigc-workbench-pilot/
  releases/<commit>/              # 本仓发布代码、业务依据和 React dist；不继承旧仓
    backend/.venv/               # 每个 release 自己的 Python 3.12 环境
    frontend/dist/               # React 生产构建，不运行 Vite server
  current -> releases/<commit>
  shared/
    pilot.json                   # 0600，基于 pilot.example.json
    openai.env                   # 0600，仅 OPENAI_API_KEY=；用户在服务器上自行编辑
    runtime/                     # 从已验证归档恢复，不能新建空库冒充迁移
      f01/workbench.sqlite3
      f01/assets/、image_versions/、c01/
      creation-decision/、c01-real/、c02-real/、c06-real/、其他原始 Evidence
    logs/                        # 0700，仅服务器维护者读取
  backups/<UTC时间>/             # 不在 runtime 内，0700
```

1. 再核对现有服务、8030、固定 IP、子网及上游路由。只用本项目新目录和独立 Label；不得修改原 8444 服务或其他项目。
2. 在独立 release 目录放入指定 Git Commit 的代码；前端用该 Commit 对应依赖构建 `npm ci && npm run build`，产物置于 `frontend/dist`。Node 只用于构建。
3. 使用现有 `/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv backend/.venv`，在该 venv 安装本 release 的 backend；不改系统 Python 或其他项目 venv。以上安装仅属于后续 P2-B。
4. 恢复已经验证的运行归档到 shared/runtime，保留 UUID、所有原始文件和历史 JSON。
5. 复制 `pilot.example.json` 为 shared/pilot.json 并设 0600；runtime_root/data_dir/frontend_dist/key_file 指向本服务的绝对路径。archived_runtime_root 来自归档 manifest 的 source_runtime_root。
6. 用户在服务器本地编辑 0600 的 shared/openai.env，不把 Key 发聊天、不写入命令参数、Git、前端或 plist。已有有效进程 Key 优先，私有文件兜底。不读取其他项目的 Key。
7. 先在前台使用本 release 的 `backend/.venv/bin/python -m aigc_workbench.pilot_server --config /Users/andy_server/apps/aigc-workbench-pilot/shared/pilot.json` 验证启动。只绑定 192.168.112.37:8030；固定同源服务 React 和 FastAPI，单进程、无 reload、无代理头信任。
8. 核实内部客户端能访问，未批准网段被拒；确认路由器没有新端口映射、现有公网代理没有新服务路由。应用网络检查是实际防线；系统防火墙/PF 状态应在 P2-B 再次核对，不能默认已隔离。
9. 再安装本目录的独立 plist 到该用户的 ~/Library/LaunchAgents，文件路径对应 release/current；shared/logs 需预先创建。以 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.song36.aigc-workbench-pilot.plist` 启动用户服务。无登录会话时不会宣称已具备无人值守开机启动；需要后续选择合适的系统服务方式。
10. 查询 `launchctl print gui/$(id -u)/com.song36.aigc-workbench-pilot`、`curl http://192.168.112.37:8030/api/health` 和 shared/logs；不将日志或 Evidence 提供为运营下载接口。轮转日志时停服务、保留带时间戳旧文件，再启动；不自动删除历史。

## 执行与故障恢复

复用现有单线程 Dispatcher：先持久化派发收据，再领取 RUNNING、创建 Run/Attempt、保存派发意图，最后调用现有模型客户端一次。
Operation ID 重放只查已有收据/Run，不再发请求；同身份改变来源或图位报冲突。
重启后 QUEUED 且未进入执行的记录转失败；已派发而未可靠终结转 OUTCOME_UNKNOWN；已经保存候选的成功结果重新可读。
未知结果保留并阻止盲目创建同图位新调用；不自动重试、切模型或重放历史修改。
失败不产生虚假候选，新候选不覆盖原 Pass。人工 Pass/Reject 和交付仍绑定具体版本。
正常停止最多等待正在执行的请求完成；启动器宽限 320 秒，plist ExitTimeOut 340 秒。强制停止可能留下结果未知，不能换新 Operation ID 自动再收费。
费用观测保留原费率来源、核对日期、规划估算、供应商 Usage 与用量费用估算；不等于最终账单，费率过时也不能伪称当前精确费用。

## 实际备份与恢复命令

仅维护者在服务器本机执行，不提供 HTTP 调试 API。先停止本服务及其他可能写同一 runtime 的开发 CLI/进程，不影响其他项目。
等待服务进程实际退出和锁释放；工具会拒绝仍持有执行锁的服务，再冻结 SQLite 写事务，使用 SQLite backup API，并完整复制 runtime 下的普通文件。
不备份 API Key、.env / .key / .pem 或活动锁/SQLite 临时文件。原始业务库、素材、候选、快照图片、独立 Evidence 均在同一归档中。

```bash
# 在本项目 current 目录执行；以下目录必须是新的，不覆盖已有归档。
backend/.venv/bin/python -m aigc_workbench.pilot_backup create \
  --runtime-root /Users/andy_server/apps/aigc-workbench-pilot/shared/runtime \
  --data-dir /Users/andy_server/apps/aigc-workbench-pilot/shared/runtime/f01 \
  --destination /Users/andy_server/apps/aigc-workbench-pilot/backups/NEW_BACKUP_NAME
backend/.venv/bin/python -m aigc_workbench.pilot_backup verify \
  --archive /Users/andy_server/apps/aigc-workbench-pilot/backups/NEW_BACKUP_NAME
backend/.venv/bin/python -m aigc_workbench.pilot_backup restore \
  --archive /Users/andy_server/apps/aigc-workbench-pilot/backups/NEW_BACKUP_NAME \
  --destination /Users/andy_server/apps/aigc-workbench-pilot/shared/NEW_RESTORED_RUNTIME
```

工具检查完整文件清单及 SHA-256、SQLite integrity/FK、各表行内容哈希、素材、快照、Run/Evidence、候选业务归属、审核和有效交付。
恢复只允许新目录，不覆盖现有运行库。先在隔离目录验证，再停服务并明确切换服务器配置指向恢复目录。
历史 Snapshot 的绝对路径通过服务器 runtime_root + archived_runtime_root 映射，原文和哈希不重写。
归档需另存至不同故障域，并定期实测恢复；本轮单机恢复测试不能证明异机备份或断电耐久已完成。

## 更新与回滚

更新前：只停本服务，确认无未终结操作（未知状态保留），创建并 verify 一份完整归档。
将新 Commit 放入全新 release 并完成测试/生产构建/独立 venv，保留旧 release 和运行归档。
停服务后以同目录临时链接再原子替换 current；不得覆盖正在运行的 release。新服务启动检查与只读浏览通过后才给运营使用。
本轮没有新增数据库 Schema，因此本次代码回退可指回上一 release 并保留 runtime；原开发服务的 localhost 访问限制不应被当作 Pilot LAN 可用性。
未来若版本有不可逆 schema 变化，须先恢复匹配的完整归档到新目录再回退代码，不能只改 current。
恢复旧归档会丢失备份之后的进展：先保留当前完整 runtime，不自动回滚或丢弃新增候选、审核与未知请求。
无论更新、恢复或回滚，启动都不能自动执行新的付费请求。

## 当前功能边界与发布判定

登录、角色、用户费用额度、次数限制及费用弹窗均不属于 Go/No-Go。
已有真实主图 V1/V2 可查看、按选定版本修改、人工审核和下载 Pass。
但新建 Task/上传/绑定本身尚不会从 Workspace 自动执行 C01～C05 并形成制作决定；没有有效决定的图位仍禁止伪造输入生图。
因此不能把当前服务描述为任意新商品完整自助闭环。本轮没有扩建该链路；P2-B 必须明确先限定已有合格图位，或另行接通新商品制作决定。
现有 V1/V2 的商品结构和精确色值仍待人工审核，Git 归档或工程通过不等于图片质量 Pass。
剩余真实发布前提为：解决上海使用与供应商支持范围不符；提供本服务私有凭证并核验账户模型访问；确认内部网络/防火墙/无公网路由；完成异机安装、数据恢复和浏览器验证。
