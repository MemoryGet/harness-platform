# 路线图：S0 – S7

## 阶段划分原则

不以"功能多少"分阶段，而以**"验证哪个假设"**分阶段。每个阶段结束必须有一个可判定的 Gate；Gate 不通过不许进入下一阶段。

核心假设链条：

```
H1 判据可被业务人员表达出来（S0–S1）
 └─ H2 判据可被机检（S1–S3）
     └─ H3 内核能同时承载多个行业（S4）  ← 最关键的一道关
         └─ H4 业务人员能自助改判据（S5）
             └─ H5 有人愿意为"可验收"付钱（S6+）
```

S4 是生死关。如果内核只能跑通一个行业，你做的不是平台，是三个项目。

---

## S0 · 判据语料采集与纸面验证（第 0–1 周）

**目标**：在写任何代码之前，先证明"判据确实存在且能被说出来"。

**任务**
1. 找 3 个不同行业的业务专家（剪辑组长 / 法务 / 客服主管各 1 人），各访谈 90 分钟。
2. 访谈只问四类问题（对应四槽位）：
   - 信息不全时你必须先问什么？
   - 你必须先查哪几个系统、按什么顺序？
   - 交付物里必须有什么，你才认可？
   - 什么情况你必须打回 / 升级 / 拒答？
3. 每次专家说"这个不行"时，**记录他的理由原话**——这是判据语料，也是未来 UI 的提示文案来源。
4. 手写三份判据 YAML（不用工具，纯手写）。
5. 让专家看 YAML 复述，确认"是不是这个意思"。

**交付物**
- `examples/*.yaml` 三份
- 访谈记录（判据语料库，Markdown）
- 一份"哪些判据专家说不清"的清单

**Gate S0**
- 三份 YAML 中每份至少有 8 条判据，四槽位均有覆盖。
- 专家对 YAML 的认可度 ≥ 80%（逐条问"这条对不对"，统计）。
- 若某行业专家说不出 8 条 → 该行业不适合做首个场景，换行业。

**不做**：不写代码、不做 UI、不选技术栈。

---

## S1 · 无头内核 MVP（第 2–4 周）

**目标**：把 YAML 变成可执行的东西，但不涉及流程编排。

**任务**
1. `core/model`：Pydantic 模型——`CriterionPack` / `Criterion` / `Checker` / `CheckResult` / `Severity`。
2. `core/dsl`：YAML 解析、字段校验、错误信息要人能看懂（业务人员会直接看到报错）。
3. `core/checkers`：四类检查器的抽象基类 + 注册表（按 `fn` 名字查找）+ 每类一个参考实现：
   - `rule` → 一个真实可跑的（视频样例用 `media.loudness`，走 ffprobe）
   - `schema` → JSON Schema 校验
   - `judge` → 调一次模型做绝对评分（先不做 pairwise）
   - `gate` → 直接返回 `pending`，等 `resume`
4. `core/cli`：`hpx validate` 与 `hpx check`（对单个产物跑某一条判据）。
5. `core/knowledge`：知识源加载（本地 YAML 目录即可）、`basis` 解析、V-KB-01~03 与 V-KB-11 校验。**S1 不引入向量库**——判据依据的检索全部按 `item_id` / `path` 精确匹配，一张表就够。

**交付物**
- 可 `pip install -e .` 的包
- `hpx validate examples/*.yaml` 全部通过
- `hpx check --criterion loudness_target --artifact demo.mp4` 能真实返回 pass/fail

**Gate S1**
- 三份 YAML 全部通过 validate，报错信息不含 Python traceback。
- 至少 3 条 `rule` 类判据能对真实样例数据返回正确结果。
- `hpx validate` 能对 `knowledge/legal-cn.yaml` + legal 样例跑通 V-KB-01~03、V-KB-11（含 quote 原文比对）。
- 单元测试覆盖：每条判据的 pass / fail / error 三种路径。

**不做**：不做流程编排、不做 DAG、不做 UI。

---

## S2 · 编译器与执行图（第 5–7 周）

**目标**：把 `workflow.steps` 编译成可执行的 DAG，支持人工闸门与断点恢复。

**任务**
1. `core/compile`：DSL → IR（中间表示）。IR 是纯 JSON，可存库、可 diff。
2. 检查器挂载规则：每条判据声明 `after: <step_id>`，编译时挂到对应节点后。
3. `core/runtime`：执行器
   - 节点执行、边路由、条件分支（`applies_when` 求值）
   - 遇 `gate` 类型检查器 → 落库为 `pending` 并暂停
   - `hpx resume <run_id> --decision approve|reject --note "..."`
4. 状态存储：SQLite（本地）→ Postgres（S6 换）。**表结构第一天就按多租户设计**（见架构文档）。
5. 失败策略：`policy.on_fail` 支持 `retry(n)` / `escalate` / `abort`。

**交付物**
- `hpx compile examples/video-shortform.yaml -o ir.json`
- `hpx run examples/video-shortform.yaml --input ./in --dry-run` 输出 DAG 与检查点拓扑
- `hpx run ... --pause-on gate` + `hpx resume` 闭环可跑

**Gate S2**
- `--dry-run` 能正确打印「节点 → 挂载的判据 → 阈值/严重级」清单，人工核对无误。
- 中断恢复：进程 kill 后 `hpx resume` 能从断点继续，不重复执行已完成节点。
- IR 两次编译同一份 YAML 结果字节一致（幂等）。

**不做**：不做并行分支、不做循环节点（S7 再说）。

---

## S3 · 验收引擎与门禁（第 8–9 周）

**目标**：让"专业级"变成可度量的数字。

**任务**
1. `core/evaluate`：golden set 管理
   - `golden/<pack_id>.yaml`：case 列表（输入、期望判定、期望命中的判据 id）
   - 支持 `expect: {criterion: loudness_target, status: fail}` 的精确断言
2. 跑分：`hpx eval examples/video-shortform.yaml --set golden/video.yaml`
3. 报告：通过率、按判据维度的失败分布、按 severity 的阻断数
4. 门禁：`hpx gate --min-pass 0.9 --max-veto 0`，CI 里返回非零退出码
5. 版本对比：`hpx eval --compare v1.0.0..v1.1.0`（判据改动的 diff 效果）
6. 知识引用校验：`check_citations` 落库，报告增加两个指标——**引用命中率**（`valid=true` 占比，即幻觉率）与**依据覆盖率**（有 `basis` 的 hard 判据占比）。

**交付物**
- `golden/*.yaml` 三份（每份 ≥ 10 条 case）
- 一份 HTML/Markdown 报告模板
- `.github/workflows/eval.yml` 示例（CI 门禁）

**Gate S3**
- 改动任意一条判据的阈值，`hpx eval --compare` 能准确显示"哪几条 case 从 pass 变 fail"。
- 门禁在通过率不足时确实阻断（用一个故意写错的版本验证）。
- 故意让 `judge` 引用一条不存在的条目，报告必须显示引用失效、该 case 判 `fail`，且不计入分数聚合。
- 单次 eval 全量跑完 < 3 分钟（不含模型调用）。

**不做**：不做自动修复建议、不做线上回流。

---

## S4 · 三行业压测（第 10 周）★ 生死关

**目标**：证明内核是平台，不是三个项目。

**任务**
1. 冻结内核代码，只允许改 `examples/` 和 `checkers/` 注册表。
2. 依次让三份 YAML 走完 validate → compile → run → eval。
3. 记录每次"不得不动内核"的冲动，写成 issue 而不是直接改。
4. 归类 issue：是 schema 缺字段？是检查器类型不够？还是业务模型错了？
5. 三份样例各挂一份最小知识库（legal 已提供 `knowledge/legal-cn.yaml`），验证同一套 `basis` / `grounding` 机制在三种产物上都成立——这是"知识绑定不是行业特性"的证明。

**Gate S4**
- 三份 YAML 在**内核零修改**下全部通过全流程。
- 每份至少 3 条检查器真实执行（不允许 mock）。
- 三份的 `artifact.kind` 覆盖至少 3 种（timeline / text / record）。
- 未解决的内核 issue ≤ 3 个，且都不阻塞主流程。

**不通过怎么办**
- 若某行业需要改 schema → 说明抽象漏了维度，现在改（纸面 + 几十行代码）。
- 若两个行业互相冲突 → 砍掉一个。宁可只支持两类，不要做四不像。

---

## S5 · 判据表单 UI（第 11–14 周）

**目标**：业务人员能自助改判据。**这是第一个 UI，且只做表单，不做画布。**

**任务**
1. 判据列表页 + 编辑表单（按四槽位分 Tab）
2. 表单字段直接来自 `ask` / `rationale` / `params`，不做自定义布局
3. 实时校验：改完立即 `validate`，错误定位到字段
4. 变更 diff 视图 + 一键回滚（对应 DSL 版本）
5. 试跑：表单里可以直接对一条样例跑 `hpx check`，看结果
6. 权限：只读 / 可编辑判据 / 可发布（三档）

**交付物**
- Web 应用（推荐 Next.js + 后端直接复用 `core` 的 Python 服务，或全 Python + HTMX 先跑通）
- 30 分钟上手视频

**Gate S5**
- **业务人员不看文档，30 分钟内独立把一条判据的阈值从 A 改成 B，并成功发布新版本。**
- 全程不需要工程师介入。
- 表单报错文案是业务语言（不允许出现 `ValidationError`、字段名 snake_case）。

**不做**：不做流程图编辑器、不做多行业视图。

---

## S6 · 行业视图与平台化（第 15–18 周）

**目标**：从"能改判据"到"能管理多个团队"。

**任务**
1. 多租户：租户隔离、判据库隔离、产物隔离、审计日志
2. 行业产物查看器插件：
   - 视频：OTIO 时间线预览 + 抽帧对照
   - 法务：条款树对照（原文 vs 判据命中）
   - 客服：会话回放 + 分派路径
3. 成本计量：按租户/按判据统计 token 与工具调用
4. 模板市场雏形：判据包可导出/导入/复制（跨租户）
5. 线上回流：真实 `fail` 与人工 `reject` 的理由进语料库

**Gate S6**
- 3 个真实团队连续使用 2 周，周活跃 ≥ 3 天。
- 判据修改中 ≥ 60% 由业务方自助完成（不经过工程师）。
- 单租户数据可完整导出并在他处导入复现。

---

## S7 · 规模化（第 19 周+）

- 并行分支、循环节点、子流程复用
- 判据包的语义化版本与依赖管理
- 判据推荐：根据历史 fail 自动建议候选判据
- 计费、SLA、私有化部署包

---

## 总览表

| 阶段 | 周期 | 核心交付 | 关键 Gate | 是否写 UI |
|---|---|---|---|---|
| S0 | 1 周 | 三份 YAML + 语料库 | 专家认可度 ≥ 80% | 否 |
| S1 | 3 周 | 判据引擎 + CLI | 3 条 rule 真实跑通 | 否 |
| S2 | 3 周 | 编译器 + 执行器 | 断点恢复可用 | 否 |
| S3 | 2 周 | 验收引擎 + 门禁 | diff 准确、能阻断 | 否 |
| S4 | 1 周 | 三行业压测报告 | **零修改跑通三行业** | 否 |
| S5 | 4 周 | 判据表单 | 业务方 30 分钟自助 | 是（仅表单） |
| S6 | 4 周 | 多租户 + 行业视图 | 3 团队连续用 2 周 | 是（插件） |

**前 10 周不写一行 UI 代码**——这是本路线图最重要的一条纪律。
