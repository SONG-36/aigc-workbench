# Quick-A Pilot 部署与运行

本轮 P2-B2 部署采用 **真实 AI 关闭、无 API Key、全新独立数据库**。不迁移开发数据库、商业素材、历史 Evidence 或测试候选，不向运营宣布正式上线。
源代码版本与生产文件 SHA-256 以每个 release 的 `release.json` 为准，现场验收记录另存服务器 `shared/deployment-record.json`。

## 当前环境与访问策略

- 目标：`andy_server@192.168.112.37`，Mac mini M4 / 16 GiB，macOS 26.4.1。
- Python：`/opt/homebrew/opt/python@3.12/bin/python3.12`，3.12.13；Node：`/opt/homebrew/opt/node/bin/node`，26.4.0。
- `~/projects` 是指向缺失挂载卷的悬空链接，禁止使用。
- `~/apps/aigc-workbench-pilot` 独立于既有 8444 legacy 服务和 Docker 等其他服务。
- 用户此前确认信任 192.168.112.0/24，但应用防火墙关闭、PF 不可读，公网转发边界尚未完整核实。
- 本轮先绑定 **127.0.0.1:8030**；只通过 SSH 转发验收，不将 `http://192.168.112.37:8030` 声称为可用 LAN 地址，不修改系统 PF、其他代理或端口映射。
- 上海部署保持真实 OpenAI 调用关闭。地区适用条件未解决前，不启用真实 AI，也不通过代理出口替代地区核查。

## 目录与版本

```text
~/apps/aigc-workbench-pilot/
  releases/<commit>/
    release.json
    backend/src/、backend/.venv/、backend/requirements-runtime.lock
    frontend/dist/、frontend/package.json、frontend/package-lock.json
    docs/                         # 能力使用的已提交依据，只读且不挂载 HTTP
    deploy/
  current -> releases/<commit>
  shared/
    pilot.json                    # 0600，无 Key，enable_real_ai=false
    runtime/f01/
      workbench.sqlite3
      assets/、staging/、image_versions/、c01/snapshot_images/
    logs/stdout.log、stderr.log    # 目录0700
    deployment-record.json
  backups/
  incoming/                       # 受控安装包及匹配平台的锁定 wheels
```

代码、venv 与持久数据分离；不覆盖已安装 release 或已有 current 目录。初始空库只由显式安装程序建立，正常服务启动仍要求数据库已存在。历史 UUID、图片与 Evidence 不因代码切换重写。

## 构建与受控传输

先提交本次工程变更，再在开发机执行：

```bash
backend/.venv/bin/python deploy/build_release.py --output .runtime/p2-b2-release.tar.gz
backend/.venv/bin/python -m pip download --index-url https://pypi.org/simple --only-binary=:all: --no-deps \
  -r backend/requirements-runtime.lock --dest .runtime/p2-b2-wheels
```

构建器从 Git HEAD 导出白名单源代码，在不含本地 `.env` 的临时导出目录构建 React；仅打包必要 backend、部署工具、业务依据、生产 dist 与依赖锁。
不传输 node_modules、本地 venv、任何 .env、.runtime、商业素材、浏览器截图或测试适配器。wheels 只包含当前锁定依赖；必须匹配服务器 Python 3.12 / arm64。
使用 SSH/SCP 将该包、安装脚本与 wheels 放入本服务新建的 incoming 目录，核对本地/远程 SHA-256。

服务器使用显式 Python 路径：

```bash
/opt/homebrew/opt/python@3.12/bin/python3.12 incoming/install_release.py \
  --root /Users/andy_server/apps/aigc-workbench-pilot \
  --bundle incoming/RELEASE.tar.gz --commit COMMIT_SHA \
  --wheels incoming/wheels
```

安装程序校验成员路径、完整文件清单和 SHA-256，创建 release 专属 venv，离线安装锁定 wheels 并运行 pip check。
release-local `.pth` 指向该 release 的 backend/src，使原有 REPOSITORY_ROOT 与业务依据路径保持有效；不安装未锁定构建工具、不复用其他项目 venv。
安装程序不切换 current、不自动启动服务、不复制 Key。更新时保留已有私有配置，拒绝本次流程启用真实 AI。

## 启动、停止及状态

首次以明确的 release Python 激活版本并安装本服务 LaunchAgent：

```bash
releases/COMMIT_SHA/backend/.venv/bin/python releases/COMMIT_SHA/deploy/pilotctl.py activate COMMIT_SHA
current/backend/.venv/bin/python current/deploy/pilotctl.py install-agent
current/backend/.venv/bin/python current/deploy/pilotctl.py start
current/backend/.venv/bin/python current/deploy/pilotctl.py status
current/backend/.venv/bin/python current/deploy/pilotctl.py restart
current/backend/.venv/bin/python current/deploy/pilotctl.py stop
```

所有命令从本服务根目录执行。Label 固定为 `com.song36.aigc-workbench-pilot`，仅控制当前用户的该服务，不使用 sudo，不改其他 Label。
LaunchAgent 通过 `gui/$(id -u)` 启动。必须有该用户 GUI 会话；KeepAlive 与当前会话内恢复需现场验证，不能据此声称退出登录、断电开机后全天运行。
停止会等待本服务执行锁释放；若仍在停止，不直接再启动或重试执行。日志位于 shared/logs，日志轮转前先停本服务并保留旧日志。

从开发机建立只绑定本机的 SSH 转发：

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18030:127.0.0.1:8030 andy_server@192.168.112.37
```

实际健康检查和浏览器验收成功后，可使用 `http://127.0.0.1:18030`；关闭 SSH 隧道会关闭该访问入口，不影响服务器 LaunchAgent。
将来直接开放 LAN 必须另行核实系统及上游边界、已有公网代理、实际客户端网段，并明确更改 bind_host/hosts/networks；本轮不执行。

## 真实 AI 关闭与 HTTP 边界

`enable_real_ai` 默认 false：启动器强制关闭 C01/C06/C07 真实执行开关，Workspace 生成及修改从后端拒绝，且不加载任何凭据。
UI 明示“模型服务暂不可用”。即使继承了进程 Key 或旧开关，禁用配置仍不能触发推理；不注入测试适配器，不制造成功候选。
保留 C01～C07 源代码、Operation ID、零隐式重试、OUTCOME_UNKNOWN、Run/Evidence、版本及 Pass 规则，后续供应商条件适用后再单独启用。

无需登录、账号角色、每日额度或额外费用审批弹窗；这不取消文件归属、SHA-256、Host、TCP 对端和同源写入校验。
拒绝 Forwarded / X-Forwarded-*；关闭 /docs、/redoc、/openapi.json，不挂载运行目录、源代码、私有配置、SQLite 或原始 Evidence。

## 备份与隔离恢复

先停止本服务；工具不自动终止其他进程，也拒绝仍持有执行锁的 runtime：

```bash
current/backend/.venv/bin/python current/deploy/pilotctl.py stop
current/backend/.venv/bin/python current/deploy/pilotctl.py backup NEW_UTC_BACKUP_NAME
current/backend/.venv/bin/python -m aigc_workbench.pilot_backup verify --archive backups/NEW_UTC_BACKUP_NAME
current/backend/.venv/bin/python -m aigc_workbench.pilot_backup restore \
  --archive backups/NEW_UTC_BACKUP_NAME --destination shared/NEW_ISOLATED_RESTORED_RUNTIME
current/backend/.venv/bin/python current/deploy/pilotctl.py start
```

既有工具冻结 SQLite 写事务并调用 SQLite backup API，覆盖 runtime 中全部业务、原图、Image Versions、Review/Pass、Snapshot、Run/Attempt/Evidence。
配置与 release 身份另保存为归档根目录的 pilot-config.json、release-version.json，其 SHA-256 在 deployment-files.json 中；不备份任何 Key 或环境密钥文件。
隔离恢复只允许新目录，不覆盖运行库。验证表内容、Task ID、Asset/Slot、图片 SHA-256、版本、Review 和 Evidence 后，再决定是否切换。
必要配置恢复时先校验 deployment-files.json，并复制到新的私有配置文件；若启用隔离恢复目录，明确修改 runtime_root/data_dir/archived_runtime_root，不能悄悄覆盖正在使用的配置。
全新部署没有历史真实候选、Review 或 AI Evidence 时应如实记录零条；不能用测试结果伪造恢复验证。备份应另存不同故障域，本轮服务器同机隔离恢复不等于异地容灾验证。

## 更新与回滚

1. 停止本服务，创建并 verify 当前完整备份，保留全部未终结及未知操作。
2. 上传新 Commit 的白名单生产包，安装到新的 releases/<commit>，不覆盖旧 release 或 shared。
3. 使用当前 Python 调用 pilotctl.py activate 新 SHA，原子替换 current；启动并检查 HTTP、资料及错误日志。
4. 若需回滚，先停止并备份现状，再 activate 已安装的旧 SHA 并启动；不自动覆盖、删除或回退业务数据。
5. 数据库 schema 不兼容时，必须将对应归档恢复到新目录并明确配置切换，不能只换代码，也不能丢弃备份之后的新增资料。

初次部署尚无历史 release，真实版本降级无法直接演练；原子切换及共享数据保留由自动化测试覆盖。所有启动、更新、恢复和回滚都不能自动发起新的付费请求。
