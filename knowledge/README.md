# 知识源样例 —— 合同审查依据

> 格式规范见 [docs/06-knowledge.md](../docs/06-knowledge.md)，配套样例 [legal-contract-review.yaml](../examples/legal-contract-review.yaml)。
> `checksum` 留空，由 `hpx kb build` 入库时计算；本文档只定义结构。

```yaml
kb_version: "0.1"
kind: KnowledgeSource

source:
  id: kb-cn-contract
  name: 民法典合同编与内部审查指引
  source_type: statute          # 混合源取最高权威级
  authority: statutory
  owner: 法务部
  pin_policy: pin_at_publish

snapshot:
  id: kb-snap-2026-11-01
  built_at: 2026-11-01T00:00:00+08:00
  checksum: null                # 由 hpx kb build 计算

items:
  # ── 法定依据（可支撑 veto）─────────────────────────────
  - id: civil-code-577
    path: 民法典/第三编合同/第八章违约责任/第577条
    title: 违约责任
    body: 当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。
    authority: statutory
    effective_from: 2021-01-01
    effective_to: null
    supersedes: null
    tags: [违约责任, 合同编]

  - id: civil-code-497
    path: 民法典/第三编合同/第二章合同的订立/第497条
    title: 格式条款无效情形
    body: 有下列情形之一的，该格式条款无效：（一）具有本法第一编第六章第三节和本法第五百零六条规定的无效情形；（二）提供格式条款一方不合理地免除或者减轻其责任、加重对方责任、限制对方主要权利；（三）提供格式条款一方排除对方主要权利。
    authority: statutory
    effective_from: 2021-01-01
    effective_to: null
    supersedes: null
    tags: [格式条款, 无效条款]

  # ── 内部强制规范（可支撑 block，不可单独支撑 veto）──────
  - id: sop-liability-cap-3-2
    path: 内部审查指引/3.2 责任限额
    title: 责任限额内控要求
    body: 销售类合同的赔偿责任累计不超过合同金额的三倍；超过三倍的，须经法务负责人书面特批。
    authority: internal_mandatory
    effective_from: 2023-07-01
    effective_to: null
    supersedes: sop-liability-cap-3-1
    tags: [责任限额, 内控]

  - id: sop-liability-cap-3-1
    path: 内部审查指引/3.1 责任限额（旧版）
    title: 责任限额内控要求（已废止）
    body: 销售类合同的赔偿责任累计不超过合同金额的两倍。
    authority: internal_mandatory
    effective_from: 2020-01-01
    effective_to: 2023-06-30
    supersedes: null
    tags: [责任限额, 已废止]

  - id: sop-prohibited-terms-4-1
    path: 内部审查指引/4.1 禁止性条款清单
    title: 禁止性条款清单
    body: 以下条款一律不得签署：无限责任、放弃诉讼权利、单方任意解除、永久排他。出现任一情形，审查立即终止并上报法务负责人。
    authority: internal_mandatory
    effective_from: 2023-07-01
    effective_to: null
    supersedes: null
    tags: [红线条款]

  - id: sop-memo-rubric-5-2
    path: 内部审查指引/5.2 风险备忘写作要求
    title: 风险备忘写作要求
    body: 风险备忘须逐条给出风险等级（高/中/低）、所依条款编号、可执行的修改建议，并区分"必须修改"与"建议修改"两类。
    authority: internal_mandatory
    effective_from: 2023-07-01
    effective_to: null
    supersedes: null
    tags: [风险备忘, 交付规范]

  # ── 案例库（仅可支撑 judge 参考与 generate，不可判定）──
  - id: case-2025-nda-017
    path: 案例库/2025/NDA-017 竞业范围争议
    title: 竞业限制范围过宽被认定无效
    body: 该案中竞业限制地域覆盖全国、期限五年，被认定超出合理范围。建议同类条款的地域不超过实际经营地，期限不超过两年。
    authority: reference
    effective_from: 2025-03-01
    effective_to: null
    supersedes: null
    tags: [竞业限制, 判例]
```

## 这份样例刻意演示的五件事

| 演示点 | 体现位置 | 校验规则 |
|---|---|---|
| 同一条款有旧版，靠 `effective_to` + `supersedes` 退出 | `sop-liability-cap-3-1` | V-KB-09 / V-KB-10 |
| `veto` 判据必须引法定依据，内部清单只能作补充 | `prohibited_clause` 挂两条 basis | V-KB-05 |
| 案例库不能当规矩 | `case-2025-nda-017` 的 `authority: reference` | V-KB-08 |
| judge 判据必须给出可校验引用 | `risk_memo_quality` 的 `grounding` | V-KB-07 |
| `quote` 必须逐字匹配原文 | 每条 basis 的 `quote` 都是 `body` 的连续子串 | V-KB-03 |

## 入库与使用

```bash
hpx kb build knowledge/legal-cn.yaml      # 计算 checksum，产出快照 kb-snap-2026-11-01
hpx kb verify kb-cn-contract              # 跑 V-KB-09/10（替代链、生效区间）
hpx compile examples/legal-contract-review.yaml   # 解析 basis → item_id，跑 V-KB-01~08、11
```
