# QA-R02 — Quick-A 制作决定交接一致性

日期：2026-11-09。范围：离线核查、最小 C06 请求组装修复；无模型请求、无新图片、无部署。

- HANDOFF_CONSISTENCY = PASS
- IMAGE_COMPLIANCE = UNVERIFIED

## 缺陷及语义分类

历史 C03～C05 输出的 `02_IMAGE_CREATION_DECISION.C05_VISUAL_CREATION.differentiation[1]`
明确选择了 A「线性车型图标替代车辆照片」和 B「不使用底部整幅蓝色横幅」。
它们属于本次候选方案采用的具体设计选择，原文没有可选、备选或条件性措辞。
方案整体仍为 `PROPOSED / REVIEW PENDING / NOT OPERATOR APPROVED`，不是运营批准或新的业务规则。

| 层级 / 字段 | A | B |
|---|---|---|
| C05 `differentiation[1]` | 明确采用 | 明确限制 |
| 同次输出的 `C06_creation_input` | 仅写图标，丢失替代照片的限定 | 未写入 |
| 持久化 `structured-result.json` / 交接 JSON 的 `image_creation_decision` | 完整保留 | 完整保留 |
| 原交接 Markdown | 仅复制制作简写，缺失限定 | 缺失 |
| 历史 C06 `context.production_instruction` / `prompt.planned_model_input.text` | 缺失限定 | 缺失 |
| 历史实际请求 `prompt` | 缺失限定 | 缺失 |
| 历史生成 PNG | 仍使用车辆照片及图标 | 仍使用底部蓝色横幅 |
| 修复后离线组装的制作指令及请求 `prompt` | 明确保留 | 明确保留 |

第一次不一致出现在同一次模型输出内部：详细 C05 与其 `C06_creation_input` 简写不一致。
Schema 已能表达两项内容，完整 JSON 保存及私有导出也均一致；不存在已证明的数据库字段丢失。
后续 `prepare_current_decision` 将简写保存为交接 Markdown，`_current_decision_sources`
仅将这段简写交给 `generation_prompt`，没有读取仍然保存在 JSON 中的 C05 执行字段。
因此不能将历史成图偏差单独归因为图像模型不遵守要求。

## 最小修复及权威边界

新 Workspace C06 请求在原制作简写之外，读取已验证、同图位 C05 的明确字段：
`layout`、`product_presentation`、`typography`、`vehicle_icon_mapping`、
`colors_and_background`、`reference_elements_to_retain`、`differentiation`。
画布仍由既有输出尺寸计划处理，1536×1536 的原生尺寸与放大记录不变。

- 保留正向设计选择和负向限制；不根据本案例硬编码图标或横幅禁令。
- 不加入 C03 分析、不采用卖点的理由、审核说明或未知字段；原文明确可选或有条件的内容保持原有语气。
- 运营当前有效的整体及逐图要求优先于 AI 简写和设计选择；运营要求之间仍遵守既有冲突规则。
- 无整体要求版本的旧任务，也保留当前逐图要求原文。商品外观、来源权限及状态规则不变。

没有调整数据库、C03～C05 输出 Schema、模型配置或 Provider。
仅在新 C06 Snapshot 的既有 `prompt` 对象内记录组装模板版本
`quick-a-c06-selected-visual-decisions-v1`，区分新组装与历史简写路径。
历史 Snapshot 继续按原组装校验；原制作决定、交接 Markdown/JSON、Run 和 Pass 文件均不回写。
复用旧简写快照时，创建新的 C06 Snapshot；不重跑 C03～C05，不重发旧 Operation ID。
带人工质量修订的早期交接路径保持原样，避免原模型设计覆盖其修订。

## 离线对照与回归

冻结输入来自既有 Terra 全链路实验；本地审查资料位于
`.runtime/QA-R02/20261109T064109Z/`，由 Git 忽略。
其中保存原文件哈希、逐字段对照、模拟制作指令及模拟最终请求。
模拟请求沿用历史模型、参数和两张原图字节，只更换组装后的 `prompt`，从未发给供应商。
完整请求、商业图片和私有 Evidence 不纳入 Git。

| 验证 | 结果 |
|---|---|
| TEST-A / B：图标替代要求及蓝色横幅限制进入 C06 | PASS |
| TEST-C：其他正常设计字段、原简写及尺寸计划保留 | PASS |
| TEST-D：有效运营要求、版本与冲突优先级保留 | PASS：组装权威声明；不代表模型实际服从已验证 |
| TEST-E：非约束分析不混入，可选措辞不升级 | PASS |
| TEST-F：不同图位的设计、参考图片及候选关联隔离 | PASS |
| TEST-G：未提供限制时不创造该限制 | PASS |
| 历史快照校验、新快照刷新及旧操作幂等 | PASS |
| 后端回归 | 378 tests passed，包括 8 项新增测试实例 |
| 前端回归 / TypeScript | 72 tests passed / PASS |
| Python AST / Git whitespace | PASS / PASS |
| 冻结的 110 个历史文件，包括数据库、原图、Snapshot 图片及候选 | 哈希保持不变 |

## 尚未验证

离线结果证明两项决定能追踪到新请求，不能证明新成图一定遵守。
本轮没有模型调用、生成或修改图片、启用运行服务的 C07，也没有 Mac mini 或 Showcase 操作。
开发后端未重启且没有 reload；下一次获准真实实验前，需要使进程加载本修复并核对运行版本。
