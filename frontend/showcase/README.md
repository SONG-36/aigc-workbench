# AIGC Workbench — 只读产品展示

这是独立静态入口，复用内部工作台的 Design Tokens、图标和图片弹窗。不导入内部 App、任务页面、API 客户端或业务 hooks。

## 本地构建与浏览

在 `frontend/` 下执行：

```sh
npm ci
npm run build:showcase
npm run preview:showcase
```

依赖已安装时跳过 `npm ci`。访问 `http://127.0.0.1:8040`。预览命令只启动 Python 静态文件服务，不启动 FastAPI，不读取私有环境或数据库。停止预览使用 Ctrl-C。

产物是 `frontend/dist-showcase/`，与内部工作台的 `frontend/dist/` 分离。相对资源路径支持单独域名或静态子目录。不需要服务器 Key、模型供应商配置或内部服务地址。

## 公开素材状态

目前 `cases.json` 的案例列表为空。现有业务图片的人工 Pass 不能证明商品、参考素材的再发布权限或保密审查已完成，因此本版本没有复制任何商业图片或内部工作台截图。

页面展示明确的待授权占位。布局图是本项目原创的界面结构示意，不是真实任务或生成成果；品牌图标复用现有界面图形。测试中的 TEST_ONLY 图形不会进入发布产物。

增加真实案例前，必须逐项核查商品、参考、成果使用许可、保密范围、明确发布授权及成果人工审核，并保存可复核依据。不要只填写布尔值来代替实际核查。

经明确许可后：

1. 只将获准图片复制到 `src/showcase/assets/approved/`，使用描述性的公开文件名（PNG、JPEG、WebP）。不要用内部 Task、Run、Asset ID 命名。
2. 在 `cases.json` 中登记公开 `slug`、标题、说明及 `images.product/reference/result`，每项含 `file`、准确的 `alt` 和 `caption`。
3. 确认 `clearance.product/reference/result/confidentiality/publication` 均为 `true`，`resultReview` 为 `HUMAN_PASSED`，`approvalRecord` 使用可公开的授权依据编号，不包含姓名、内部记录 ID 或保密资料。
4. 重新检查文字、图内信息和文件元数据。构建必须通过；未列入清单的图片、缺失许可、外部 URL、路径穿越、额外私有字段均不得发布。

## 隔离检查

`build:showcase` 同时执行类型检查、静态构建和产物核查：

- 独立配置不加载 `.env`，不配置 API 代理，关闭公共目录自动复制及 source map。
- 模块导入只允许 Showcase 及列明的共享 UI 组件；不允许导入内部运营模块。
- 图片只允许来自明确列入清单的本地授权文件及原创品牌图标；按 SHA-256 核查产物。
- 产物只包含 HTML、JS、CSS、明确许可图片、构建检查清单及列明的静态发布规则，不包含私有配置、数据库、Prompt、Evidence 或源码映射。
- 页面 CSP 禁止网络连接、表单提交、嵌入页面及外部脚本。页面本身不含上传、生成、修改、审核写入入口。

可以独立停止静态服务、切换静态发布目录来更新或回滚；不得回滚或替换内部工作台的数据。

## D04 Cloudflare Pages Direct Upload

只发布 `frontend/dist-showcase/`，不连接 GitHub、不启动内部后端、不转发 Mac mini。不上传整个工程。构建显式输出 `_headers`、`404.html`、`robots.txt`；没有 Functions、Worker、数据库绑定或 API 代理。

Pages HTTPS 响应使用 `_headers`：CSP 只允许本站静态资源，禁止连接、表单、脚本内联及 iframe 嵌入；同时发送 nosniff、no-referrer、DENY 和禁用敏感设备能力的 Permissions-Policy。HTML 每次重新验证缓存，带哈希资源缓存一年。页面 meta、响应头和 robots.txt 均标记不索引。顶层 `404.html` 防止 Pages 自动启用 SPA 路由回退。实际响应仍须在每次部署后核查。

### 认证与发布

本次使用官方 npm 包 Wrangler `4.158.0`，不加入应用依赖。凭证由 Wrangler 私有存储管理，不写入仓库、命令参数或日志。首次 OAuth 授权需由账号持有人确认；仅请求账户/用户读取及 Pages 管理权限，Background Access 由官方流程附加，不请求 DNS 或 Tunnel 写权限。

在仓库根目录执行：

```sh
npm exec --yes --package wrangler@4.158.0 -- wrangler login --scopes account:read user:read pages:write
npm exec --yes --package wrangler@4.158.0 -- wrangler whoami
npm exec --yes --package wrangler@4.158.0 -- wrangler pages project list --json
npm --prefix frontend run build:showcase
```

确认账号和项目清单后，使用专门的 Showcase 项目。没有可复用项目时才创建；不得覆盖其他网站。以下名称须先核实可用：

```sh
npm exec --yes --package wrangler@4.158.0 -- wrangler pages project create aigc-workbench-showcase --production-branch main
npm exec --yes --package wrangler@4.158.0 -- wrangler pages deploy frontend/dist-showcase --project-name aigc-workbench-showcase --branch d04-preview --commit-hash "$(git rev-parse HEAD)"
npm exec --yes --package wrangler@4.158.0 -- wrangler pages deployment list --project-name aigc-workbench-showcase --json
```

`d04-preview` 与生产分支分离。记录 CLI 实际返回的 Deployment ID、不可变预览 URL、分支别名、提交 SHA 和逐文件 SHA-256；命令示例不证明部署已经成功。部署后通过外部 HTTPS、浏览器及网络记录核查页面、全部资源、安全头、未知路径、只读交互和无内部 API 请求。发布记录和截图可保存在忽略的本地 QA 目录，禁止包含认证信息。

### 域名和发布状态

先验收 `pages.dev`。`studio-demo.enterprise-ai-workspace.com` 只是候选自定义域名，未获明确配置确认时不创建 Pages 域名或 DNS 记录。不得修改 `gj-demo.enterprise-ai-workspace.com`、国金 Tunnel 或其他服务。当前没有商业案例公开授权；技术预览可用时只能标记 `PUBLIC TECHNICAL PREVIEW READY`。

### 更新、回滚与停止

更新前保存当前成功部署 ID、URL、来源提交和完整静态包；只重新上传核查通过的产物。保留历史成功部署，不执行删除或清理。

预览部署的哈希 URL 不随后续发布变化。预览不适用 Pages 生产回滚按钮；需要恢复分支别名时，核对并重新上传上一份成功静态包到同一预览分支。生产发布（后续单独决定）可在 Pages → Deployments 对历史成功的**生产**部署使用 `Rollback to this deployment`。不可把预览部署当作生产回滚目标。停止本地预览使用 Ctrl-C；撤下公网预览须由持有人在独立 Pages 项目中操作，不影响内部工作台。

官方依据：[Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/)、[响应头](https://developers.cloudflare.com/pages/configuration/headers/)、[404 行为](https://developers.cloudflare.com/pages/configuration/serving-pages/)、[预览](https://developers.cloudflare.com/pages/configuration/preview-deployments/)、[回滚限制](https://developers.cloudflare.com/pages/configuration/rollbacks/)。
