# Mac mini Showcase origin

独立静态托管，不启动 FastAPI，不读取内部 Task、图片、数据库或环境配置。复用 D03 页面及完整构建字节，Pages 保留为备用技术预览；此工具不会修改 Pages 项目。

## 边界和原理

- 目标：`/Users/andy_server/apps/aigc-showcase/{releases,current,logs}`。
- 静态服务：`127.0.0.1:8040`，LaunchAgent `com.song36.aigc-showcase`。
- 使用目标机已有 `/usr/bin/python3`，无新 Python/Node 依赖。服务器启动时逐文件验 SHA-256、拒绝符号链接，将清单内静态字节载入内存；请求不会按任意路径读取磁盘。
- 只支持 GET/HEAD。其他方法拒绝，目录、私有文件及未知路由返回 404，不回退到运营应用。当前 Showcase 使用 hash 导航，没有需映射为首页的路径路由。
- 使用原产物 `_headers` 的 CSP、noindex 和缓存规则；`_headers` 本身不公开。每个响应附带 Origin、Release、Request-ID，可与 Mac mini 的 JSON 访问日志关联验证来源。
- Origin 额外发送 `Cache-Control: no-transform`，阻止 Cloudflare 自动注入统计脚本及改写 HTML，保持原始字节与严格 CSP。此设置只作用于本站响应，不修改 Zone 的统计或其他站点配置。依据：[Cloudflare Web Analytics](https://developers.cloudflare.com/web-analytics/get-started/)。
- 独立 Tunnel 仅允许 `studio-demo.enterprise-ai-workspace.com → http://127.0.0.1:8040`，兜底 404。不得包含内部 8030 或国金路由。
- 国金为现有本地管理单副本 Tunnel；本次独立运行，避免为改 ingress 重启它。使用已有 cloudflared，不安装、卸载或更改原服务。
- LaunchAgent 运行在目标用户 GUI 登录会话。需要实际验证启动/重启；不能据此宣称退出登录或断电重启后仍全天运行。

## 构建及发布

先核对 Git、目标目录、8040、launchd 标签、DNS 和 Tunnel 清单。记录原国金配置 SHA-256、进程及站点响应；候选域名必须无冲突。发布只包含 8 个已核查静态文件、清单、版本记录和两个独立服务控制脚本，不含整个仓库。

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

根据**实际返回**的 UUID 和凭证路径填写 `cloudflared.example.yml`，保存到服务器私有目录，权限 600。不覆盖 `~/.cloudflared/config.yml`。用 `cloudflared tunnel --config PRIVATE_CONFIG ingress validate` 核对；新建独立 LaunchAgent `com.song36.aigc-showcase-tunnel`，关闭自动更新，使用独立日志和 `127.0.0.1:20242` metrics。进程仅使用新 Tunnel 凭证，不在命令参数中放 Token。

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

停止或重启只能针对以上新建标签。首次迁移没有更早的 Mac mini Showcase release 可回滚；保留当前成功 release 作为后续基线，并继续保留原 Pages URL，不自动配置跳转或删除。

## 公网验收

从 MacBook 访问新的 HTTPS hostname，检查全体资源字节和 Mac mini release 一致，读取 `X-Showcase-Request-ID` 后在服务器访问日志找到同一 ID，并核对本机监听 PID。此证据与 DNS 指向独立 Tunnel 一起证明页面来自 Mac mini，而非 Pages。

浏览器检查交互、响应式、控制台及 HAR，仅允许新 hostname 的静态 GET。验证 `/api/tasks`、生成、审核、Evidence、环境文件和数据库路径无法访问。再检查国金 HTTPS、内部 8030、原配置 SHA-256/进程与 Pages 成功版本未变。分别重启本静态服务和新 Tunnel，再次验证公网及版本恢复。真实案例授权仍待确认；没有新增商业图片或 AI 调用。

官方依据：[本地管理 Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/)、[Tunnel DNS](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/)。
