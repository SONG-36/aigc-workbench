# Mac mini Showcase origin

独立静态托管，不启动 FastAPI，不读取内部 Task、图片、数据库或环境配置。复用 D03 页面及完整构建字节，Pages 保留为备用技术预览；此工具不会修改 Pages 项目。

## 边界和原理

- 目标：`/Users/andy_server/apps/aigc-showcase/{releases,current,logs}`。
- 静态服务：`127.0.0.1:8040`，LaunchAgent `com.song36.aigc-showcase`。
- 使用目标机已有 `/usr/bin/python3`，无新 Python/Node 依赖。服务器启动时逐文件验 SHA-256、拒绝符号链接，将清单内静态字节载入内存；请求不会按任意路径读取磁盘。
- 只支持 GET/HEAD。其他方法拒绝，目录、私有文件及未知路由返回 404，不回退到运营应用。Showcase 保留 hash 导航；`/cases` 是明确列出的静态首页别名，使用同一份已校验 HTML 和根目录资源，供真实案例入口访问，不开启通用 SPA 路由回退。
- 使用原产物 `_headers` 的 CSP、noindex 和缓存规则；`_headers` 本身不公开。每个响应附带 Origin、Release、Request-ID，可与 Mac mini 的 JSON 访问日志关联验证来源。
- Origin 额外发送 `Cache-Control: no-transform`，阻止 Cloudflare 自动注入统计脚本及改写 HTML，保持原始字节与严格 CSP。此设置只作用于本站响应，不修改 Zone 的统计或其他站点配置。依据：[Cloudflare Web Analytics](https://developers.cloudflare.com/web-analytics/get-started/)。
- 独立 Tunnel 仅允许 `studio-demo.enterprise-ai-workspace.com → http://127.0.0.1:8040`，兜底 404。不得包含内部 8030 或国金路由。
- 国金为现有本地管理单副本 Tunnel；本次独立运行，避免为改 ingress 重启它。使用已有 cloudflared，不安装、卸载或更改原服务。
- LaunchAgent 运行在目标用户 GUI 登录会话。需要实际验证启动/重启；不能据此宣称退出登录或断电重启后仍全天运行。

## 构建及发布

先核对 Git、目标目录、8040、launchd 标签、DNS 和 Tunnel 清单。记录原国金配置 SHA-256、进程及站点响应。发布只包含经白名单与哈希校验的静态文件、清单、版本记录和两个独立服务控制脚本，不含整个仓库。

```sh
npm --prefix frontend run build:showcase
python3 -m unittest discover -s deploy/showcase -p 'test_*.py' -v
# 先提交部署源码，再打包，输出路径须不存在；保存输出的 SHA-256。
python3 deploy/showcase/build_release.py /tmp/aigc-showcase-RELEASE.tar.gz
scp /tmp/aigc-showcase-RELEASE.tar.gz andy_server@192.168.112.37:/tmp/
ssh andy_server@192.168.112.37 \
  'python3 - install /tmp/aigc-showcase-RELEASE.tar.gz --sha256 ARCHIVE_SHA256' \
  < deploy/showcase/control.py
```

安装拒绝覆盖同名 Release、不同的已有 launchd 定义或非符号链接 current；解包只接受清单中的普通文件，检查传输包及逐文件哈希。切换 current 后重启**本服务**并校验 HTTP 响应的 Release；有旧版本时启动失败自动切回原指针。不会清理旧 release 或其他文件。

## 本机验收后才启用 Tunnel

先在 Mac mini 检查根页、全部资源及其哈希、安全头、写方法、敏感路径、重启恢复。确认本机 HTTP 全部通过后再创建独立 Tunnel：

```sh
# 以下命令在 Mac mini 执行；只使用该机已有 cert.pem，不复制或打印凭证。
/opt/homebrew/bin/cloudflared tunnel list --output json
/opt/homebrew/bin/cloudflared tunnel create aigc-showcase
```

根据**实际返回**的 UUID 和凭证路径填写 `cloudflared.example.yml`，保存到服务器私有目录，权限 600。不覆盖 `~/.cloudflared/config.yml`。用 `cloudflared tunnel --config PRIVATE_CONFIG ingress validate` 核对；使用同目录 `com.song36.aigc-showcase-tunnel.plist` 模板，将 `__ROOT__` 替换为独立部署目录、`__TUNNEL_UUID__` 替换为实际新 UUID，保存到目标用户的 `~/Library/LaunchAgents/`。关闭自动更新，使用独立日志和 `127.0.0.1:20242` metrics。进程仅使用新 Tunnel 凭证，不在命令参数中放 Token。

Tunnel 连通后，只创建已核查的新 hostname：

```sh
/opt/homebrew/bin/cloudflared tunnel route dns ACTUAL_NEW_UUID studio-demo.enterprise-ai-workspace.com
```

不使用覆盖 DNS 的选项。出现权限、重名或冲突时停止公网步骤，保留本机静态服务。不得修改国金 DNS、原 Tunnel 配置、原进程或 Pages。首次公网 HTTPS 可能需等待证书激活，不能关闭证书校验绕过。

## 服务操作及回滚

在 Mac mini 执行：

```sh
cd /Users/andy_server/apps/aigc-showcase
/usr/bin/python3 current/control.py status
/usr/bin/python3 current/control.py start
/usr/bin/python3 current/control.py stop
/usr/bin/python3 current/control.py restart
# 查看已安装 release 后明确指定；不复制或覆盖旧目录。
/usr/bin/python3 current/control.py activate PREVIOUS_RELEASE_ID
tail -n 30 logs/access.log
tail -n 30 logs/error.log

launchctl print gui/$(id -u)/com.song36.aigc-showcase-tunnel
launchctl kickstart -k gui/$(id -u)/com.song36.aigc-showcase-tunnel
launchctl bootout gui/$(id -u)/com.song36.aigc-showcase-tunnel
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.song36.aigc-showcase-tunnel.plist
```

停止或重启只能针对以上新建标签。首次安装前没有旧 Showcase release；每次更新保留旧目录，但回滚只选已经通过公网验收的版本。保留当前成功 release 作为后续基线，并继续保留原 Pages URL，不自动配置跳转或删除。

## 公网验收

从 MacBook 访问新的 HTTPS hostname，检查全体资源字节和 Mac mini release 一致，读取 `X-Showcase-Request-ID` 后在服务器访问日志找到同一 ID，并核对本机监听 PID。此证据与 DNS 指向独立 Tunnel 一起证明页面来自 Mac mini，而非 Pages。

浏览器检查首页和 `/cases` 的图片、交互、控制台及 HAR，仅允许本站静态 GET。验证 `/api/tasks`、生成、审核、Evidence、环境文件和数据库路径无法访问。再检查国金 HTTPS、内部 8030、原配置 SHA-256/进程与 Pages 成功版本未变。日常静态更新只重载 Showcase 静态服务，不改 Tunnel；迁移时的 Tunnel 重启验证见下方历史记录。

## D05-C01 发布范围

D05-04 按用户明确决定发布 D05-02 当前案例。三张图片的运营批准、确切文件和 SHA-256 见 [C01 记录](../../frontend/showcase/reviews/c01-20261107.md)。保留 `OPERATOR_APPROVED_FOR_PUBLIC_SHOWCASE`、`NEEDS_RIGHTS_REVIEW` 及参数未独立验证状态，不将发布成功等同于独立法律或产品性能核查。

构建仅注入 Git 忽略发布目录中的这三张白名单原文件。首页及 `/cases` 提供同一只读 Showcase，图片切换及放大复用 D05-02；不重新设计页面，不改变图片，不更新 Pages，也不修改内部 8030 或任何 Tunnel 路由。

官方依据：[本地管理 Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/)、[Tunnel DNS](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/)。

## 迁移验收记录 — 2026-11-07

- `OLD_PAGES_URL`：[原 Pages 技术预览](https://aigc-workbench-showcase.pages.dev/)，项目及 Deployment `654f74be-753f-4997-ae34-1929b3bb85f6` 保留，未重新部署、覆盖、回滚、删除或设置跳转。继续采用方案 A，作为备用技术预览。
- `NEW_MAC_MINI_URL`：[Mac mini 托管入口](https://studio-demo.enterprise-ai-workspace.com/)，HTTPS 公网验收通过。
- 根目录：`/Users/andy_server/apps/aigc-showcase`；current 指向 `releases/740adc45e785c79ea250434367b39a6141817230-20261107T040713Z`。运行代码来源提交 `740adc45e785c79ea250434367b39a6141817230`。
- 静态服务：`com.song36.aigc-showcase`，最终验收 PID `83351`，仅监听 `127.0.0.1:8040`。独立日志位于根目录 `logs/`。
- Tunnel：`aigc-showcase`，UUID `6baea38a-e417-49cb-9d19-3994b3275fe5`，本地管理；LaunchAgent `com.song36.aigc-showcase-tunnel`，最终验收 PID `83353`。配置为 `private/cloudflared.yml`（600，父目录 700），凭证留在服务器 `~/.cloudflared/`（600），未复制至开发机或 Git。
- DNS：只新增 `studio-demo` 指向上述新 Tunnel。Zone 记录从 2 条变为 3 条，国金 DNS 及 Tunnel 路由保持原样。新 ingress 中没有 8030、国金或其他应用。
- 8 个产物文件与 Pages 基线一致。公网提供的 7 个资源逐一 SHA-256 一致；首页为 `534e62c8186dbaba34a2c998480157021b7dab5566accb113e02832e6fe723a1`。未改 Showcase UI 或加入商业素材。
- 本机 HTTP、全部资源、安全头、GET/HEAD、缓存、拒绝写入、路径隔离、哈希/符号链接保护及传输包拒绝测试通过（6 项自动化测试及真实部署检查）。公网 Task/生成/审核 API、私有配置、数据库、服务日志与 Evidence 均不可读取或写入：404/405；编码遍历返回 400。
- 来源证明：公网响应 `X-Showcase-Origin: mac-mini`、Release 和 Request-ID 与服务器访问日志匹配；最终证据 Request-ID `f23be807-b7a9-4889-b844-5a9b10277861`，对应 PID 83351 的 Release。请求没有访问 Pages Origin。
- 浏览器：MacBook 直接使用新 HTTPS 域名，4 个同源静态 GET 均 200，交互后没有内部 API/外部脚本请求，控制台 0 消息；案例占位切换、布局放大、Escape/焦点恢复及 1440/1280/1024/768px 截图复核通过。真实案例仍待公开授权。
- 静态服务和新 Tunnel 已分别重启，PID 均改变，Release 和文件字节保持。Tunnel 重连后的第一次公网 GET 曾返回 530，后续复查为 200；确切中断时长未测量，不承诺无中断重启。当前用户 GUI 登录会话已验证，退出登录及整机断电重启未验证；未重启整机或其他服务。
- 国金原进程 PID 39943、原配置 SHA-256 `9c32b3aa43cd97903a123b952a8eb348d69cfc68c9fa742c7ab880688815f608` 未变，原 3100 内容哈希未变，国金公网 HTTPS 200。内部 8030 原进程 PID 90854、配置、launchd、release 指针和首页哈希未变，LAN 访问仍 200；新公网没有其业务 API。
- 回滚基线：当前已验收的 `740adc45…-20261107T040713Z` 和对应静态包保留。初始 `0ccb26a1…-20261107T040425Z` 也保留，但缺少 no-transform，会发生统计脚本注入，不作为合格公网回滚目标。无更早合格的 Mac mini 公网版本；本轮未实际降级，Pages 继续可用。
- 证据：开发仓忽略目录 `.runtime/d04-migration-20261107T035915Z/`，含产物清单、完整静态包、安装/重启/回归结果、来源日志关联、公开 HTTP 检查、HAR 与截图。没有凭证、商业数据库或原始 AI Evidence。
- 本轮付费 AI 调用 0，未 Push，未修改旧仓、内部业务代码、模型配置、历史 Snapshot、图片、Review 或 Pass。
