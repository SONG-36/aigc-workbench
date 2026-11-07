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
- 产物只包含 HTML、JS、CSS、明确许可图片和构建检查清单，不包含数据库、配置、Prompt、Evidence 或源码映射。
- 页面 CSP 禁止网络连接、表单提交、嵌入页面及外部脚本。页面本身不含上传、生成、修改、审核写入入口。

可以独立停止静态服务、切换静态发布目录来更新或回滚；不得回滚或替换内部工作台的数据。

## D04 部署所需配置（本轮未执行）

只发布 `dist-showcase/`，不要发布整个工程或连接内部服务。D04 需单独确认域名、HTTPS、静态目录、缓存策略、响应安全头及发布回滚目录。

建议服务器额外发送 `Content-Security-Policy: frame-ancestors 'none'`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`。`frame-ancestors` 必须由 HTTP 响应头设置，不能依靠页面 meta。HTML 使用短缓存，带哈希资源可长期缓存。未知路径和 `/api/` 请求返回 404；POST 等写方法应拒绝。不得反向代理到内部运营端口。

本轮不配置公网域名、Cloudflare 或 Tunnel，不更新 Mac mini。
