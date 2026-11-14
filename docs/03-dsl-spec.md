# 判据 DSL 规范 v0.1

> 状态：草案（S0 定稿目标）。规范版本号写在 `dsl_version`，与判据包版本 `metadata.version` 相互独立。

## 1. 设计目标

| 目标 | 落地方式 |
|---|---|
| 业务人员能读懂 | 每条判据必须有 `ask`（业务语言提问）与 `title`（中文短标题） |
| 可静态校验 | 不允许表达式里出现函数调用，只允许字段比较与逻辑组合 |
| 可 diff | 纯文本、字段顺序稳定、id 不可变 |
| 行业无关 | 载荷类型由 `artifact.kind` 声明，内核不含任何行业逻辑 |
| 可编译 | 每条判据声明挂载点 `after`，编译成 DAG 上的检查点 |

## 2. 顶层结构

```yaml
dsl_version: "0.1"                # 必填，规范版本
kind: CriterionPack               # 必填，固定值
metadata:                         # 必填
  id: video-shortform             # snake_case，全局唯一（租户内）
  name: 短视频批量出片
  industry: media                 # 自由文本，仅用于分组展示
  version: 1.0.0                  # 语义化版本，发布后不可改
  owner: 内容中台组
  description: 抖音/视频号竖屏短片的出片规范
artifact:                         # 必填，产物契约
  kind: timeline                  # text | timeline | record | table
  format: otio
  schema_ref: null                # kind=record/table 时可指向 JSON Schema
evidence:                         # 声明本包需要哪些证据（运行时收集）
  - probe                         # 媒体探测结果
  - transcript                    # 词级转写
  - ai_metadata                   # 生成式内容标记
knowledge:                        # 可选，判据依据层，见 §11 与 06-knowledge.md
  sources:
    - id: kb-platform-spec
      name: 平台技术规范与法定披露要求
      source_type: standard       # statute | standard | internal_sop | case_library | draft
      authority: regulatory       # statutory | regulatory | internal_mandatory | internal_advisory | reference
      pin_policy: pin_at_publish  # pin_at_publish | pin_at_run | follow_latest
      required: true
  require_basis_for_block: true   # block 级判据是否必须挂依据
slots:                            # 四槽位，见 §4
  input: []
  process: []
  quality: []
  veto: []
workflow:                         # 流程，见 §5
  steps: []
policy:                           # 失败策略
  on_fail: retry                  # retry | escalate | abort
  max_retries: 1
eval:                             # 验收配置
  golden_set: golden/video.yaml
  min_pass_rate: 0.9
  max_veto: 0
```

## 3. 判据（Criterion）

四槽位里的元素结构完全一致，只是语义不同。

```yaml
- id: loudness_target            # 必填，snake_case，包内唯一，发布后不可改名
  title: 响度达标                 # 必填，中文短标题（≤ 12 字）
  ask: "成片响度应该是多少 LUFS？容差多少？"   # 访谈问题，S5 表单直接用它做 label
  rationale: "各平台响度标准不同，超标会被限流。"  # 为什么重要，审计与 judge rubric 复用
  evidence: [probe]              # 本条判据依赖哪些证据
  basis:                         # 可选，判据依据（见 §11）；block/veto 建议必填
    - source: kb-platform-spec
      item: lufs-standard-2026
      path: 技术规范/音频/响度
      quote: "成片整体响度应控制在 -14 LUFS，容差 ±1 LU"
      required: true
  applies_when: null             # 可选，条件表达式，见 §6
  after: export                  # 必填，挂载到哪个 step 之后
  check: {...}                   # 必填，见 §4
  severity: block                # 必填，见 §7
```

字段约束：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 不可变。改名视为"删除旧判据 + 新增判据"，会破坏历史统计 |
| `title` | 是 | ≤ 12 字，UI 列表用 |
| `ask` | 是 | 业务语言，用专家访谈的原话更好 |
| `rationale` | 建议 | 空着不影响运行，但会让 `judge` 判据质量下降 |
| `evidence` | 否 | 空表示不需要额外证据（如纯结构断言） |
| `basis` | 否 | 判据依据，指向知识库条目；`veto` 必填，`block` 由 `knowledge.require_basis_for_block` 决定，见 §11 |
| `applies_when` | 否 | 空表示恒适用 |
| `after` | 是 | 必须是 `workflow.steps[].id` 中存在的值 |
| `severity` | 是 | 见 §7 |

## 4. 四槽位语义

| 槽位 | 回答的问题 | 时机 | 失败后果 |
|---|---|---|---|
| `input` | 信息够不够？缺什么必须先问？ | 运行开始前 | 通常 `warn`（追问补料）或 `block`（无法开始） |
| `process` | 必须按什么顺序、查哪些系统？ | 每个 step 执行后 | `block`（流程不合规，需重做） |
| `quality` | 交付物必须包含什么？ | 最终产物生成后 | `block`（不可发布） |
| `veto` | 什么情况必须停手？ | 任意时刻 | `veto`（终止 + 强制人工复核） |

**四槽位的检查器类型偏好**（不是强制）：

| 槽位 | 常用类型 |
|---|---|
| input | `rule`（字段存在性）、`schema` |
| process | `rule`（步骤顺序、工具调用记录） |
| quality | `rule`（指标）、`schema`、`judge`（风格） |
| veto | `rule`（合规硬条件）、`gate`（人工确认） |

## 5. 检查器（Checker）规范

### 5.1 `rule` —— 采集 + 声明式断言

```yaml
check:
  type: rule
  fn: media.probe                  # 必填，注册表中的采集函数
  params: {}                       # 传给 fn 的参数
  assert:                          # 必填，至少一条断言
    - field: video.duration_sec
      op: "<="
      value: 60
      message: "时长超过 60 秒，平台会截断"
```

`fn` 只负责**采集证据**并返回 JSON，`assert` 负责**判定**。分离的理由：业务人员能看懂 `assert`（所以他们能改阈值），而 `fn` 是工程师写的采集器（他们不需要改）。

支持的 `op`：

| op | 含义 | value 示例 |
|---|---|---|
| `==` `!=` | 相等 | `"9:16"` |
| `<` `<=` `>` `>=` | 数值比较 | `60` |
| `within` | 容差区间 | `[-15, -13]` |
| `in` `not_in` | 枚举 | `["mp4", "mov"]` |
| `contains` | 集合/字符串包含 | `"AI生成"` |
| `matches` | 正则 | `"^(1080\|720)x1920$"` |
| `min_count` | 最小数量 | `3`（如"至少 3 条字幕"） |

多条 `assert` 之间是 **AND**；需要 OR 时用 `applies_when` 拆成两条判据。

### 5.2 `schema` —— 结构合法性

```yaml
check:
  type: schema
  schema_ref: schemas/shortform_timeline.json
  strict: true                     # 严格模式：不允许额外字段
```

### 5.3 `judge` —— 模型评审

```yaml
check:
  type: judge
  mode: absolute                   # absolute | pairwise
  model: vlm-default               # 逻辑名，映射到具体模型版本，记录在结果里
  rubric:                          # 评分维度，来自 rationale 的拆解
    - 画面主体是否居中且在安全区内
    - 字幕是否遮挡人脸
  scale: 1-5
  pass_if: "score >= 4"
  samples: 3                       # 采样次数，取中位数，降低抖动
  reference: null                  # pairwise 模式下的参考样本 uri
  grounding:                       # 可选，知识引用约束，见 §11.3
    mode: cite_required            # none | cite_allowed | cite_required
    sources: [kb-platform-spec]    # 允许引用的源；空表示不限制（不推荐）
    min_citations: 1
    on_invalid_citation: fail      # fail | downgrade_to_warn | retry_once
    allow_quote_fuzzy: false
```

要求：
- `judge` 结果必须记录 `model_version`、`temperature`、`samples`、每次的原始分数。
- **`judge` 不允许出现在 `veto` 槽位**——否决权必须落在确定性规则或人手上。
- `grounding.mode=cite_required` 时，输出必须是结构体 `{status, score, reason, citations[], uncited_claims[]}`；`uncited_claims` 非空或引用数不足 → 直接 `fail`，不进入打分聚合。

### 5.4 `gate` —— 人工闸门

```yaml
check:
  type: gate
  approver_role: 剪辑组长
  timeout_min: 30
  on_timeout: escalate             # escalate | auto_reject
  must_see:                        # 审批人必须看到的证据（UI 会置顶展示）
    - evidence.transcript
    - check_results[quality.*]
  prompt: "确认成片可对外发布？"
```

`must_see` 是防"橡皮图章"的设计：审批前强制展示关键证据，且展示行为会被记录。

### 5.5 `workflow.steps` —— 步骤定义

```yaml
workflow:
  steps:
    - id: transcribe              # 必填，snake_case，包内唯一
      uses: tool.asr_word_level   # 必填，注册表中的执行器名（tool.* / agent.* / gate.*）
      output: transcript          # 可选，产出的证据键名，供 evidence 引用
      depends_on: [ingest]        # 可选，显式依赖；省略时按声明顺序串行
      config:                     # 可选，传给执行器的参数
        language: zh
        word_timestamps: true
```

字段约束：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 判据的 `after` 引用它，改名等于破坏所有挂载点 |
| `uses` | 是 | 命名空间约定：`tool.*` 确定性工具、`agent.*` 模型调用、`gate.*` 人工节点 |
| `output` | 否 | 同名 output 不允许重复，否则证据会被覆盖 |
| `depends_on` | 否 | 不允许成环；省略时按数组顺序串行（保守策略，S2 只支持串行 + 扇出并行） |
| `config` | 否 | 执行器自行解释，内核不校验内容（避免行业逻辑进内核） |

**编译规则**：`depends_on` 为空数组时视为依赖前一个 step；有 `depends_on` 时以显式声明为准。拓扑排序失败（成环）时，报错必须指出环上的 step 链。

## 6. `applies_when` 表达式

极简语法，只允许：字段路径、字面量、比较、`and` / `or` / `not`、括号。

```yaml
applies_when: "input.platform in ['douyin', 'shipinhao']"
applies_when: "evidence.transcript.word_count > 200 and artifact.kind == 'timeline'"
```

**禁止**：函数调用、正则、算术、嵌套对象构造。理由：要能被业务人员读懂、能被静态求值、能被 IDE 提示。

可引用的命名空间：

| 前缀 | 内容 |
|---|---|
| `input.*` | 运行输入参数 |
| `artifact.*` | 产物元信息（kind / format / duration 等） |
| `evidence.*` | 已声明的证据键 |
| `env.*` | 平台、渠道等环境变量（白名单） |

## 7. 严重级（Severity）

| severity | 运行时行为 | 是否阻断发布 | 谁处理 | 能否重试绕过 |
|---|---|---|---|---|
| `info` | 记录 | 否 | 无人 | — |
| `warn` | 记录 + 报告标黄 | 否 | 建议产出方处理 | — |
| `block` | 阻断当前节点，按 `policy.on_fail` 处理 | 是 | 产出方修复 | 可以（修复后重跑） |
| `veto` | 立即终止流程，状态 `aborted` | 是 | 合规 / 主管人工复核 | **不可以** |

`veto` 的语义是"这条不接受机器自愈"，必须走 `gate` 人工确认后才能继续。典型用途：侵权素材、敏感内容、法定披露缺失。

## 8. 编译产物（IR）

`hpx compile` 输出的 JSON，是所有视图与运行时的唯一输入。

```json
{
  "ir_version": "0.1",
  "pack": {"id": "video-shortform", "version": "1.0.0", "checksum": "sha256:..."},
  "artifact": {"kind": "timeline", "format": "otio"},
  "graph": {
    "nodes": [
      {"id": "ingest", "uses": "tool.probe_media", "output": "probe"},
      {"id": "export", "uses": "tool.otio_export", "output": "timeline", "depends_on": ["assemble"]}
    ],
    "edges": [["ingest", "transcribe"], ["transcribe", "assemble"]]
  },
  "checkpoints": [
    {
      "criterion_id": "loudness_target",
      "slot": "quality",
      "after": "export",
      "severity": "block",
      "checker": {"type": "rule", "fn": "media.probe", "assert": [{"field": "audio.integrated_lufs", "op": "within", "value": [-15, -13]}]}
    }
  ],
  "knowledge": {
    "pins": [{"source": "kb-platform-spec", "snapshot": "kb-snap-2026-10-15"}],
    "bindings": [
      {"criterion_id": "loudness_target", "slot": "quality", "source": "kb-platform-spec",
       "item": "lufs-standard-2026", "path": "技术规范/音频/响度",
       "quote_sha256": "4b81...", "required": true}
    ]
  },
  "gates": [{"criterion_id": "final_review", "after": "export", "approver_role": "剪辑组长"}],
  "eval": {"golden_set": "golden/video.yaml", "min_pass_rate": 0.9, "max_veto": 0}
}
```

编译规则：

1. `slots.*[].after` 决定挂载位置；编译后 `checkpoints` 按拓扑序排列。
2. 同一节点上多条判据的执行顺序：`veto` → `block` → `warn` → `info`（先杀后判，省算力）。
3. `applies_when` 编译成检查点的前置条件，运行时求值。
4. 编译必须幂等：同一份 YAML 两次编译输出字节一致（字段排序固定）。

## 9. 校验错误信息规范

业务人员会直接看到报错，所以错误信息必须是业务语言。

```json
{
  "ok": false,
  "errors": [
    {
      "path": "slots.quality[0].check.assert[0].op",
      "code": "UNKNOWN_OP",
      "message": "「响度达标」这条判据用了不支持的比较方式 「about」",
      "hint": "可用：== != < <= > >= within in not_in contains matches min_count",
      "criterion_id": "loudness_target"
    }
  ]
}
```

**禁止**出现：Python traceback、`ValidationError`、snake_case 字段名裸露、英文缩写（除业界通用如 LUFS）。

## 10. 向后兼容策略

| 变更类型 | 是否允许 | 处理 |
|---|---|---|
| 新增可选字段 | 允许 | 小版本号 +1 |
| 新增 op / 检查器类型 | 允许 | 小版本号 +1 |
| 修改字段语义 | 不允许 | 必须新字段 + 迁移脚本 |
| 删除字段 | 不允许 | 标记 `deprecated`，保留两个大版本 |
| 修改 `severity` 默认值 | 不允许 | 显式必填，不给默认 |

`dsl_version` 升级时提供 `hpx migrate` 命令，把旧 YAML 自动升到新规范并输出 diff。

## 11. 知识绑定（Knowledge Grounding）

完整设计见 [06-knowledge.md](06-knowledge.md)。本节只定 DSL 层的契约。

### 11.1 三种用途，不可混用

| 用途 | 位置 | 参与判定 | 检索方式 |
|---|---|---|---|
| 判据依据 | `criterion.basis` | 是 | 精确（item_id / path） |
| 检查依据 | `check.grounding` | 是 | 精确（限定源 + 条目集） |
| 生成上下文 | `step.config.retrieval` | **否** | 语义（向量召回） |

铁律：**判定链路上的知识访问只允许精确检索**。向量召回的结果不得进入 `check_results`，否则同输入无法复现，`eval` 与审计同时失效。

### 11.2 `knowledge` 块

| 字段 | 必填 | 说明 |
|---|---|---|
| `sources[].id` | 是 | 包内唯一，被 `basis.source` 引用 |
| `sources[].source_type` | 是 | `statute` / `standard` / `internal_sop` / `case_library` / `draft` |
| `sources[].authority` | 是 | `statutory` > `regulatory` > `internal_mandatory` > `internal_advisory` > `reference` |
| `sources[].pin_policy` | 是 | `pin_at_publish`（推荐）/ `pin_at_run` / `follow_latest` |
| `sources[].required` | 否 | `true` 时源不可用则整个包不可运行，禁止静默降级 |
| `sources[].provider` | 否 | 外部知识源接入声明，见 §11.6；本地快照源可省略 |
| `require_basis_for_block` | 否 | 默认 `false`；`true` 时所有 `block` 判据必须挂 `basis` |

### 11.3 `basis` 与 `grounding`

`basis[]` 字段：`source`（必填）、`item` 或 `path`（至少一个，需唯一解析）、`quote`（可选，做原文子串比对）、`required`（默认 `true`）。

`grounding` 字段：`mode`（`none` / `cite_allowed` / `cite_required`）、`sources`（允许引用的源白名单）、`min_citations`、`on_invalid_citation`、`allow_quote_fuzzy`。

### 11.4 编译与运行时语义

1. **编译期解析**：`basis` 解析为具体 `item_id` + 快照，写入 IR 的 `knowledge.bindings`；解析失败即编译失败，不推迟到运行时。
2. **快照锁定**：run 创建时把 `knowledge.pins` 写入 `run_knowledge_pins`，此后所有知识读取限定在该快照内。
3. **运行时零检索**：判定只用 IR 里已绑定的 `item_id` 取文本，不做相似度计算。
4. **引用闭环**：`judge` 返回的每条 `citation` 必须通过四项校验——条目存在于锁定快照、当前时间点生效、`quote` 为原文子串、源权威级不低于 `internal_mandatory`。任一失败按 `on_invalid_citation` 处置，默认 `fail`。

### 11.5 校验规则

| 编号 | 规则 | 级别 |
|---|---|---|
| V-KB-01 | `basis.source` / `grounding.sources` 必须已在 `knowledge.sources` 声明 | error |
| V-KB-02 | `item` / `path` 必须能在快照中唯一解析 | error |
| V-KB-03 | `quote` 必须是条目 `body` 归一化后的连续子串 | error |
| V-KB-04 | `pin_policy: follow_latest` 的源不得被 `basis` 引用 | error |
| V-KB-05 | `veto` 判据至少有一条 `required: true` 的依据，其权威级 ∈ {`statutory`, `regulatory`} | error |
| V-KB-06 | 无 `basis` 的 `veto` 判据不可发布；`block` 判据由 `require_basis_for_block` 决定 | error / warn |
| V-KB-07 | `cite_required` 时 `min_citations ≥ 1` | error |
| V-KB-08 | `grounding.sources` 权威级不得低于 `internal_mandatory` | error |
| V-KB-09 | `supersedes` 链不得成环，替代关系时间必须单调 | error |
| V-KB-10 | 同源同 `path` 的条目生效区间不得重叠 | error |
| V-KB-11 | `basis` 指向条目的生效区间必须覆盖包发布日 | error |

### 11.6 外部源接入（`provider`）

用户自带知识库（文档目录或 MCP 服务）时的声明。完整拓扑与权限模型见 [07-knowledge-integration.md](07-knowledge-integration.md)。

```yaml
knowledge:
  sources:
    - id: kb-customer-sop
      source_type: internal_sop
      authority: internal_mandatory
      pin_policy: pin_at_publish
      provider:
        type: mcp                  # mcp | filesystem | object_storage
        server: notion-kb          # 连接器注册表里的名字，不写 URL、不写凭证
        scope: "workspace:acme/space:sop"
        fetch_full_text: true      # 必须拉全文，否则 quote 校验不可用
      sync:
        mode: scheduled            # manual | scheduled | on_demand
        cron: "0 3 * * *"
        on_change: new_snapshot    # new_snapshot | block_publish
```

三条硬约束：

1. **凭证不入 DSL。** 只写 `server` 名字，凭证由连接器注册表按租户加密存储——判据包会被导出、分享、提交 git。
2. **`fetch_full_text: false` 的源不得被 `basis` 引用**（只索引片段等同 `follow_latest`，按 V-KB-04 处理）。
3. **`provider` 只描述"怎么同步进来"，不描述"运行时怎么读"。** 判定一律读本地快照；内核的 `Provider` 接口不暴露 `search`，从类型层面禁止判定链路做模糊检索。

## 12. 复合任务（`subtasks`）

完整设计见 [08-composition.md](08-composition.md)。本节只定 DSL 契约。

### 12.1 产物变为复合

```yaml
artifact:
  kind: composite
  parts:
    - slot: video_cut
      kind: timeline
      format: otio
      required: true
    - slot: article
      kind: text
      format: markdown
      required: true
```

`kind: composite` 时 `parts` 必填；每个 `slot` 全局唯一，且必须被子任务的 `produces` 覆盖（V-COMP-01）。

### 12.2 `subtasks[]` 字段

```yaml
subtasks:
  - id: video
    pack: video-shortform@~1.1      # 必填，可含版本约束
    inputs:                          # 必填，映射到子包的 input 槽位
      platforms: "${run.inputs.platforms}"
    produces: video_cut              # 必填，对应 artifact.parts[].slot
    acceptance:                      # 可选，父对子的验收门槛
      min_pass_rate: 0.9
      max_veto: 0
    on_fail: block                   # block | warn | continue
    depends_on: []                   # 可选，只表达顺序，不传数据
    timeout_min: 90
    foreach: null                    # 可选，同构扇出
    as: null                         # foreach 时的循环变量名
    collect: list                    # list | first | merge
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 包内唯一；判据可用 `after: subtask:<id>` 挂到子任务终态之后 |
| `pack` | 是 | `id@版本约束`；生产包不得省略版本（V-COMP-05） |
| `inputs` | 是 | 必须能填满子包 `input` 槽位的必填判据，否则编译失败（V-COMP-03） |
| `produces` | 是 | 必须是 `artifact.parts[].slot` 之一 |
| `acceptance` | 否 | 父对子的判定标准，与子包自身的 `eval` 相互独立 |
| `on_fail` | 否 | 默认 `block`；`warn`/`continue` 要求对应 part `required: false`（V-COMP-02） |
| `depends_on` | 否 | 只表达执行顺序；子任务间传数据必须走父包 `inputs` |
| `foreach` | 否 | 必须是可静态求值的列表（`run.inputs.*` 或常量） |
| `collect` | 否 | `list`（默认）/ `first` / `merge`，决定聚合判据可访问的字段 |

### 12.3 失败传播（不可配置的部分）

| 子任务结果 | 上浮为 |
|---|---|
| `veto` | 父 `veto`（**强制**，无开关） |
| `block` / `failed` | 按父 `on_fail` |
| `warn` | 仅记录，不上浮 |

### 12.4 IR 表达

子任务编译为独立节点类型，与 `steps` 分属两层：

```json
"subtasks": [
  {"id": "video", "kind": "subrun", "pack": {"id": "video-shortform", "version": "1.1.2"},
   "produces": "video_cut", "depends_on": [], "on_fail": "block",
   "acceptance": {"min_pass_rate": 0.9, "max_veto": 0}}
],
"aggregate": {"after": ["video", "copy", "legal"], "parts": ["video_cut", "article", "legal_report"]}
```

编译规则：子包版本在编译期解析为确定值写入 IR；父包的 `workflow.steps` 排在所有 `subrun` 节点之后。

### 12.5 校验规则

| 编号 | 规则 | 级别 |
|---|---|---|
| V-COMP-01 | `produces` 与 `parts[].slot` 双向一一覆盖 | error |
| V-COMP-02 | `on_fail: warn/continue` 的子任务，对应 part 必须 `required: false` | error |
| V-COMP-03 | 子任务 `inputs` 必须能填满子包 `input` 槽位必填项 | error |
| V-COMP-04 | 被引用 pack 不得含 `subtasks`（禁止三层嵌套） | error |
| V-COMP-05 | 生产包不得省略 `pack` 版本号 | error |
| V-COMP-06 | `depends_on` 不得成环、不得悬空 | error |
| V-COMP-07 | `foreach` 必须可静态求值，不得引用其他子任务产物 | error |
