# 知识库绑定（Knowledge Grounding）

> 状态：草案 v0.1。配合 `03-dsl-spec.md` 一起读；本文件定义"判据需要依据时"框架如何处理。
> 适用范围：`kind: CriterionPack` 的 `knowledge` 块、判据的 `basis`、`judge` 的 `grounding`。

## 1. 一句话定位

知识库不是第五个槽位，也不是"接个向量库做 RAG"，而是**判据的依据层**。它只回答一个问题：

> 这条判据凭什么成立？这个"凭什么"能不能复现、能不能追溯、会不会失效？

判据回答"怎么判"，知识库回答"凭什么这样判"。两者分离，才能让判据从"某位专家的偏好"升级为"可审计的组织规则"。

## 2. 三种用途，必须先分开

| 用途 | DSL 位置 | 消费者 | 是否参与判定 | 检索方式 |
|---|---|---|---|---|
| **判据依据** | `criterion.basis` | 编译器 / 审计视图 | 是（决定判据是否成立） | 精确（按 item_id / path） |
| **检查依据** | `check.grounding` | `judge` 检查器 | 是（引用不合法则判定作废） | 精确（限定源 + 条目集） |
| **生成上下文** | `step.config.retrieval` | `agent.*` 步骤 | **否** | 语义（向量召回允许） |

**两条铁律：**

1. 判定链路上的知识访问**只允许精确检索**——按 `item_id`、`path`、`tag` 过滤，不允许向量召回。
2. 向量召回只服务生成层。其召回结果不得写入 `check_results`，也不得作为 `pass/fail` 的依据。

理由：向量检索天然不确定——索引重建、切分策略调整、embedding 模型升级都会改变召回结果。一旦它进入判定链，同一份输入在不同时间会得到不同结论，`eval` 失去基线意义，审计也无法回答"当时为什么这么判"。

## 3. 四个反模式（为什么不能只接一个向量库）

| 反模式 | 表现 | 后果 |
|---|---|---|
| **把库当上下文** | 每个 step 检索 top-k 拼进 prompt | 不可复现：同输入不同结论 |
| **引用不可追溯** | judge 说"违反行业惯例"却不给出处 | 不可问责：审片人/法务不认 |
| **新旧版本并存** | 规范改版后库里两份都在 | 不可失效：模型引旧版 → 合规事故 |
| **无依据硬判** | 判据只写阈值，不写来源 | 无法辩论：业务方只会说"这不是我们规矩" |

第 3 条最危险，因为它在测试环境几乎不会暴露：只有规范真正更新之后，问题才会以"某天起所有单子都判错"的形式爆发。

## 4. 数据模型

```sql
-- 知识源：一个可独立版本化的文本集合（法条库、内部 SOP、案例库）
knowledge_sources(
  tenant_id, source_id, name, source_type, authority_level,
  owner, pin_policy, uri, created_at,
  PRIMARY KEY (tenant_id, source_id)
)

-- 快照：源自带的版本，按源独立打版（法条与 SOP 更新节奏不同）
knowledge_snapshots(
  tenant_id, source_id, snapshot_id, built_at, item_count,
  checksum, status,          -- status: building | active | retired
  PRIMARY KEY (tenant_id, source_id, snapshot_id)
)

-- 条目：可被引用的最小单位，必须带结构化路径
knowledge_items(
  tenant_id, snapshot_id, item_id, path, title, body,
  effective_from, effective_to, supersedes, authority_level,
  tags_json, checksum,
  PRIMARY KEY (tenant_id, snapshot_id, item_id)
)

-- 切片：只服务生成层（语义检索），不参与判定
knowledge_chunks(
  tenant_id, snapshot_id, item_id, chunk_no, text, embedding,
  PRIMARY KEY (tenant_id, snapshot_id, item_id, chunk_no)
)

-- 编译产物：判据 → 条目 的绑定关系（发布时固化）
pack_knowledge_bindings(
  tenant_id, pack_id, pack_version, criterion_id,
  source_id, item_id, path, quote_sha256, required,
  PRIMARY KEY (tenant_id, pack_id, pack_version, criterion_id, source_id, item_id)
)

-- 运行锁定：本次 run 用的是哪份快照
run_knowledge_pins(
  tenant_id, run_id, source_id, snapshot_id, resolved_at,
  PRIMARY KEY (tenant_id, run_id, source_id)
)

-- 引用留痕：judge 引了什么，验过没有
check_citations(
  tenant_id, run_id, criterion_id, source_id, item_id,
  quote, valid, invalid_reason, created_at
)
```

三个设计要点：

1. **快照按源独立**。法条一年改几次、内部 SOP 一季度改一次、案例库每天新增——不能用一个全局版本号绑死。run 锁的是一个 **pin 集合**，不是单一版本。
2. **条目必须带 `path`**，形如 `民法典/第三编合同/第八章/第577条`。没有路径就只能引用到"某份 PDF 的某一段"，审阅者无法定位，等于没引用。
3. **`supersedes` 表达替代链**，并且必须保证同源同 `path` 在任一时刻只有一条生效条目。这条约束在入库期强制（见 §7 V-KB-09/10），不能留给运行时。

## 5. DSL 扩展

### 5.1 顶层 `knowledge` 块

```yaml
knowledge:
  sources:
    - id: kb-cn-contract
      name: 民法典合同编 + 内部审查指引
      source_type: statute           # statute | standard | internal_sop | case_library | draft
      authority: statutory           # statutory | regulatory | internal_mandatory | internal_advisory | reference
      owner: 法务部
      pin_policy: pin_at_publish     # pin_at_publish | pin_at_run | follow_latest
      required: true                 # 源不可用 → 整个包不可运行，禁止静默降级
  require_basis_for_block: true      # block 级判据是否强制挂依据
```

用户自带的知识库（文档目录，或 Notion / 语雀 / 自建系统通过 MCP 暴露）通过 `sources[].provider` 声明接入方式，在入库期同步物化成本地快照后再参与判定——**判定链路不直连外部服务**。接入拓扑、Provider 接口与权限模型见 [07-knowledge-integration.md](07-knowledge-integration.md)。

`pin_policy` 语义：

| 取值 | 含义 | 允许被 `basis` 引用 |
|---|---|---|
| `pin_at_publish` | 发布时锁定当时最新快照，写进包版本 | 是（默认，推荐） |
| `pin_at_run` | 每次运行锁当时最新快照，记录在 run 上 | 是，但同一包不同 run 可能不同结论 |
| `follow_latest` | 不锁，永远取最新 | **否**，只允许生成层使用（V-KB-04） |

### 5.2 判据级 `basis`

```yaml
- id: liability_cap_policy
  title: 责任限额符合内控
  ask: "赔偿责任上限最多可以是合同金额的几倍？"
  rationale: "内控要求责任上限不超过合同金额 3 倍，超出需特批。"
  basis:
    - source: kb-cn-contract
      item: sop-liability-cap-3-2
      path: 内部审查指引/3.2 责任限额
      quote: "赔偿责任累计不超过合同金额的三倍"
      required: true
  after: segment
  check:
    type: rule
    fn: clause.value_check
    params: { clause_key: liability_cap }
    assert:
      - field: cap_multiple
        op: "<="
        value: 3
        message: "责任限额超过合同金额 3 倍，需走特批"
  severity: block
```

字段约束：

| 字段 | 必填 | 说明 |
|---|---|---|
| `source` | 是 | 必须已在该包 `knowledge.sources` 中声明 |
| `item` | 二者之一 | 条目 id，编译期解析为具体快照内的条目 |
| `path` | 二者之一 | 结构化路径；`item` 缺失时按 `path` 在源内唯一解析，多处命中则编译失败 |
| `quote` | 否 | 给了就做原文子串比对（归一化空白后），防止"引了条目但曲解原文" |
| `required` | 否 | 默认 `true`；依据缺失/过期时本条判据不得判 `pass` |

### 5.3 `judge` 的 `grounding`

```yaml
check:
  type: judge
  mode: absolute
  model: llm-default
  rubric:
    - 每条风险是否引用了具体条款号
    - 是否区分了"必须改"与"建议改"
  scale: 1-5
  pass_if: "score >= 4"
  samples: 3
  grounding:
    mode: cite_required          # none | cite_allowed | cite_required
    sources: [kb-cn-contract]    # 允许引用的源；空表示不限制（不推荐）
    min_citations: 1
    on_invalid_citation: fail    # fail | downgrade_to_warn | retry_once
    allow_quote_fuzzy: false     # 默认严格子串匹配
```

`cite_required` 时 judge 必须输出结构化结果，而非自由文本：

```json
{
  "status": "fail",
  "score": 2,
  "reason": "责任限额条款缺失，且付款条件不完整",
  "citations": [
    {
      "source": "kb-cn-contract",
      "item": "sop-liability-cap-3-2",
      "path": "内部审查指引/3.2 责任限额",
      "quote": "赔偿责任累计不超过合同金额的三倍"
    }
  ],
  "uncited_claims": ["该条款偏离行业惯例"]
}
```

判定规则：`uncited_claims` 非空或 `citations` 数量 < `min_citations` → 直接 `fail`，不进入打分聚合。这比"扣分"更硬，因为无法举证的判断本质上不是判断。

### 5.4 编译产物（IR 增量）

```json
{
  "knowledge": {
    "pins": [
      {"source": "kb-cn-contract", "snapshot": "kb-snap-2026-11-01"}
    ],
    "bindings": [
      {
        "criterion_id": "liability_cap_policy",
        "slot": "quality",
        "source": "kb-cn-contract",
        "item": "sop-liability-cap-3-2",
        "path": "内部审查指引/3.2 责任限额",
        "quote_sha256": "9f2c...",
        "required": true
      }
    ]
  }
}
```

编译后 `basis` 已解析为具体 `item_id` + 快照，**运行时不再检索**。这是"判定可复现"的工程实现。

## 6. 四条运行机制

### 6.1 快照锁定

`run` 创建时，按包的 `pins` 写入 `run_knowledge_pins`。此后该 run 的所有知识读取都限定在这批快照内，即使源在运行中途更新也不受影响。

- `pin_at_publish`：用包内固化的快照，与发布时的判定完全一致（回归测试必须用这个）。
- `pin_at_run`：用运行时最新快照，但差异会记录进报告（`knowledge_drift` 字段），供对比。

### 6.2 编译期绑定（运行时零检索）

`hpx compile` 阶段完成三件事：

1. 按 `basis` 解析出 `item_id`，命中不到即**编译失败**（不是运行时报错）。
2. 校验 `quote` 是否为条目原文的连续子串（V-KB-03）。
3. 把 `item` 的 `checksum` 与 `quote_sha256` 写进 IR，形成不可篡改的绑定。

好处：判据包发布时，它引用的每一条依据都已被验证存在且原文一致。运行时只需按 id 取文本，不涉及相似度计算。

### 6.3 引用闭环校验

`judge` 返回后，校验器逐条验四件事：

| 检查 | 失败含义 | 默认处置 |
|---|---|---|
| 条目存在于锁定快照 | 模型编造了条目 id | `fail`，记 `invalid_reason=not_found` |
| 条目在当前时间点生效 | 引了已废止版本 | `fail`，`invalid_reason=expired` |
| `quote` 是条目原文子串 | 引了条目但篡改了原文 | `fail`，`invalid_reason=quote_mismatch` |
| 源权威等级 ≥ `internal_mandatory` | 拿案例/草案当规矩 | `fail`，`invalid_reason=authority_too_low` |

四类失败都写入 `check_citations`。这张表是后续一切改进的数据源：哪条判据的引用最常失效、模型最爱编哪类条目、哪个源的引文最难匹配。

### 6.4 入库期冲突与失效检测

知识源构建快照时（`hpx kb build`）执行：

- 同源同 `path` 不得有生效区间重叠的条目（否则"当前生效版本"不唯一）；
- `supersedes` 链不得成环，被替代条目的 `effective_to` 必须早于新条目 `effective_from`；
- 条目 `body` 为空、`path` 重复、`effective_from` 缺失 → 该条目拒收。

包发布时（`hpx publish`）额外执行：

- 所有 `basis` 指向的条目，其生效区间必须覆盖发布日（防止发布即过期）；
- 覆盖率检查：`veto` 判据必须有依据（V-KB-05/06）。

## 7. 校验规则清单

| 编号 | 规则 | 级别 |
|---|---|---|
| V-KB-01 | `basis.source` 与 `grounding.sources` 必须已在 `knowledge.sources` 声明 | error |
| V-KB-02 | `basis` 的 `item`/`path` 必须能在快照中唯一解析 | error |
| V-KB-03 | `basis.quote` 非空时必须是条目 `body` 归一化后的连续子串 | error |
| V-KB-04 | `pin_policy: follow_latest` 的源不得被任何 `basis` 引用 | error |
| V-KB-05 | `veto` 判据至少有一条 `required: true` 的依据，其权威级 ∈ {statutory, regulatory} | error |
| V-KB-06 | 无 `basis` 的 `veto` 判据不可发布；无 `basis` 的 `block` 判据由 `require_basis_for_block` 决定 | error / warn |
| V-KB-07 | `grounding.mode=cite_required` 时 `min_citations ≥ 1` | error |
| V-KB-08 | `grounding.sources` 的权威级不得低于 `internal_mandatory` | error |
| V-KB-09 | 同源 `supersedes` 链不得成环，替代关系时间必须单调 | error |
| V-KB-10 | 同源同 `path` 的条目生效区间不得重叠 | error |
| V-KB-11 | `basis` 指向条目的生效区间必须覆盖包发布日 | error |

V-KB-06 是产品层面最有价值的一条：它**逼着业务专家交代依据**。当专家填不出 "这条红线来自哪里"，说明要么这条判据是他的个人偏好（应该降级为 `warn`），要么组织缺少书面规范（这本身是需要被暴露的管理问题）。这正是访谈环节最好的追问点。

## 8. 知识库自身的三个质量指标

判据层做得再好，知识层腐烂就全盘失效。三个必须上报表的指标：

| 指标 | 定义 | 告警线（建议） | 说明 |
|---|---|---|---|
| **覆盖率** | 有 `basis` 的 hard 判据 / 全部 hard 判据 | < 80% | 直接反映"规矩有没有出处" |
| **新鲜度** | 已废止但仍在库中被引用的条目数、源更新到入库的平均滞后天数 | 引用数 > 0 即告警 | 法规更新的滞后是合规风险 |
| **引用命中率** | `check_citations` 中 `valid=true` 的比例 | < 90% | **这是"幻觉率"的实体指标**，比任何主观评测都准 |

第三项尤其重要。它把"模型有没有胡说"从感觉变成了可观测数据，而且按判据、按源、按模型版本三个维度都能下钻。

## 9. 在路线图中的落点

知识库不单独占一个阶段，它渗透在前四个阶段里：

| 阶段 | 知识库相关工作 | 注意 |
|---|---|---|
| **S1** | `knowledge_sources` / `items` 表；`basis` 字段；离线校验（V-KB-01~03） | 知识以**本地 YAML/Markdown 目录**形式存在即可，**不要引向量库** |
| **S2** | 快照锁定（`run_knowledge_pins`）；生成层的 `retrieval` 配置 | 此时才需要 embedding，且只服务 `agent.*` |
| **S3** | `check_citations`、引用闭环校验、覆盖率门禁进 CI | 引用命中率进验收报告 |
| **S4** | 三份样例各挂一份小知识库，压测同一内核 | 这才是真正的压测点：法务挂法条、视频挂平台规范、客服挂升级 SOP |
| **S6** | 知识源接入（法规订阅、文档同步）、租户级知识库管理 UI | 多租户下"共享法条库 + 租户 SOP"需分层 |

**S1–S3 不要引入向量数据库。** 判据驱动的检索绝大部分是精确查找（按 path、按 tag、按生效期），一个 SQLite 表加 `json_extract` 就够。过早引入向量库会让你把"检索"当成核心，而核心其实是"引用可验证"——这两件事的工程量差了十倍。

## 10. 什么时候不需要知识库

| 场景 | 是否需要 | 正确处理 |
|---|---|---|
| 判据全靠确定性指标（响度、时长、格式、字段完整性） | 不需要 | 直接 `rule`，不挂 `basis` |
| 需要"参考案例"给人看 | 不需要 | 放生成层 `retrieval`，不进判定 |
| 需要实时业务数据（今日库存、当前汇率、客户余额） | **不是知识库** | 这是**数据源**，走 `tool.*` 查询，结果作为 `evidence` |
| 规范/法条/内部制度这类低频变更的权威文本 | 需要 | 本章范围 |

最后一行和倒数第二行的区分很关键：

- **知识库 = 低频变更、有权威性、需要引用出处的文本。** 它的价值在"可引用"。
- **数据源 = 高频变更、按 key 查询、无需引用出处的业务数据。** 它的价值在"当时准确"。

把数据源塞进知识库（比如每天把价目表灌进向量库），会让知识库退化成一个糟糕的 ETL 管道：既查不准，又说不清版本。

## 11. 三个待定问题（S3 前需定）

1. **多源冲突以谁为准？** 国标与行标冲突、内部 SOP 与法条冲突时，是按 `authority` 自动排序，还是要求显式声明 `priority`？建议：`authority` 定级 + 包内显式 `priority` 覆盖，冲突时在报告里显式列出，不静默取值。
2. **依据被废止后，历史判定怎么处理？** 之前依据旧版判 `fail` 的单子，是标记"依据已失效"还是触发重判？建议：只标记不重判（历史不可变），但统计时单列。
3. **租户级与平台级知识如何分层？** 法条库应全平台共享（省成本、防各租户自行维护出错），租户 SOP 必须隔离。建议：`knowledge_sources.tenant_id = '__shared__'` 表示平台级，读取时并入租户视图，但租户不可写。
