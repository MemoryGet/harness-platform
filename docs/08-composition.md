# 复合任务与子任务组合

> 状态：草案 v0.1。本文定义"一个业务由多个子任务组合运行、最后汇总成一个结果"的支持范围与机制。
> 相关：[03-dsl-spec.md](03-dsl-spec.md) §12、[02-architecture.md](02-architecture.md) §2.2。

## 1. 结论先行

**支持，但只支持"契约式组合"，不支持"嵌套工作流引擎"。**

区别在于：父包不描述子任务内部有几个 step、什么顺序、怎么循环——它只声明四件事：

1. 有哪些子任务，各自用哪个判据包（`pack`）
2. 每个子任务交付到哪个产物槽位（`produces`）
3. 父对子的验收门槛（`acceptance`）
4. 汇总后要判什么（`composite` 判据）

子包对父包是黑盒。这条约束不是偷懒，而是防止框架退化成 BPMN——一旦允许父层编排子层内部，你就得实现子图、跨层变量、跨层条件、跨层异常，最后没有一个业务人员能读懂自己的流程。

## 2. 三种"多子任务"必须分开

用户说"很多子任务"时，往往混着三种完全不同的形态。它们的机制、风险、适用场景都不一样：

| 形态 | 例子 | 机制 | 支持的语义 |
|---|---|---|---|
| **A. 同构批量扇出** | 10 个平台各出一条短片；50 份合同批量审 | `foreach` | 同一判据包，N 个独立子 run，聚合判定 |
| **B. 异构复合** | 新车上市内容包 = 视频 + 图文 + 法务审查 | `subtasks` + `composite` | 不同判据包，不同产物类型，最后汇总 |
| **C. 多层嵌套** | 复合任务里还有复合任务 | `subtasks`（限制 2 层） | 父 → 子，子不得再声明 `subtasks` |

前两种必须用不同机制，不能合并：A 是"同一标准跑 N 遍"，B 是"不同标准各跑一遍再对齐"。混用会导致 `foreach` 里塞进异构内容，最终变成"用 YAML 写 for 循环 + if 分支"。

C 只支持两层，理由见 §7。

## 3. DSL：`subtasks` 块

### 3.1 异构复合

```yaml
dsl_version: "0.1"
kind: CriterionPack
metadata:
  id: launch-content-package
  name: 新车上市内容包
  version: 1.0.0

artifact:
  kind: composite                  # 关键：产物是复合的
  parts:
    - slot: video_cut
      kind: timeline
      format: otio
    - slot: article
      kind: text
      format: markdown
    - slot: legal_report
      kind: record
      schema_ref: schemas/legal_report.json

subtasks:
  - id: video
    pack: video-shortform@~1.1     # 版本约束，见 §3.3
    inputs:
      platforms: ${run.inputs.platforms}
      raw_footage: ${run.inputs.footage_uri}
    produces: video_cut
    acceptance:                    # 父对子的验收门槛
      min_pass_rate: 0.9
      max_veto: 0
    on_fail: block                 # block | warn | continue

  - id: copy
    pack: launch-copy@1.0.0
    inputs:
      model_spec: ${run.inputs.spec}
      tone: ${run.inputs.tone}
    produces: article
    acceptance: {min_pass_rate: 0.9, max_veto: 0}
    on_fail: block

  - id: legal
    pack: legal-contract-review@1.0.0
    inputs:
      contract: ${run.inputs.legal_doc}
      contract_type: sales
    produces: legal_report
    acceptance: {min_pass_rate: 0.95, max_veto: 0}
    on_fail: block
    depends_on: [copy]             # 可选：子任务之间的顺序约束
```

要点：

- `produces` 必须对应 `artifact.parts[].slot`，一一对应，不允许孤儿（V-COMP-01）。
- `depends_on` 只表达**顺序**，不传递数据。子任务之间传数据必须经过父包的 `inputs` 映射，不能"儿子直接读儿子的产物"——否则契约就漏了。
- `acceptance` 是父层视角的门槛，与子包自己的 `eval` 相互独立。子包内部的 eval 是它自己的质量门，父层只关心"你给我的是不是合格的交付"。

### 3.2 同构批量扇出

```yaml
subtasks:
  - id: per_platform
    pack: video-shortform@~1.1
    foreach: ${run.inputs.platforms}   # ["douyin", "shipinhao", "youtube_shorts"]
    as: platform                       # 循环变量名，可在 inputs 中引用
    inputs:
      platform: ${platform}
      raw_footage: ${run.inputs.footage_uri}
    produces: video_cut
    collect: list                      # list | first | merge
    acceptance: {min_pass_rate: 0.9, max_veto: 0}
    on_fail: block
```

`collect` 决定聚合语义：

| 取值 | 含义 | 聚合判据可用字段 |
|---|---|---|
| `list` | 保留全部子产物（默认） | `count`, `items[]` |
| `first` | 取第一个通过验收的 | `first` |
| `merge` | 按 `merge_key` 合并为一份（如多平台字幕合并成一张表） | `merged` |

扇出的现实价值不在"省事"，而在**失败隔离**：3 个平台里 1 个失败，`on_fail: block` 只阻断那一个子 run，另外两个照常出片，父报告标出哪一个没成。这比"整批重跑"便宜一个数量级。

### 3.3 版本约束

`pack: video-shortform@~1.1` 支持三种写法：

| 写法 | 含义 | 适用 |
|---|---|---|
| `@1.1.0` | 精确锁定 | 生产、审计要求严格 |
| `@~1.1` | 允许 1.1.x 补丁级 | 默认推荐（判据阈值微调不需要重发父包） |
| `@^1.0` | 允许 1.x | 内部试用 |
| 省略 | 取当前最新 | 仅限原型，生产禁止（V-COMP-05） |

编译时把解析结果写进 IR，运行记录实际用的子包版本——否则审计时无法回答"这次跑的是哪版判据"。

## 4. 汇总判据：composite 的三种类型

父层的判据和单任务的判据写法完全一致（同一套四槽位、四类检查器），差别在于**可访问的数据**多了一层 `parts.*`：

| 类型 | 判什么 | 例子 | 检查器 |
|---|---|---|---|
| **完整性** | parts 是否齐全、是否过验收 | 三个 part 都存在，且各自 `max_veto = 0` | `rule` |
| **一致性** | 跨 parts 的同一事实是否一致 | 视频口播价 = 图文价 = 合同价；车型参数三处一致 | `rule` + 抽取函数 |
| **全局指标** | 整体层面的量 | 总时长、总成本、总字数、总文件体积 | `rule` |

一致性判据值得单独说：**这是复合任务独有的能力，也是这类业务真正的痛点。** 现实事故大多不是"某一份做得不好"，而是"几份之间不一致"——法务把责任限额改到 2 倍，销售 PPT 还写着 3 倍，视频口播说的是"无上限"。单任务框架永远发现不了这类问题，因为它从不同时持有多个产物。

```yaml
slots:
  quality:
    - id: price_consistency
      title: 价格口径一致
      ask: "视频、图文、合同里的价格必须一致吗？以哪个为准？"
      rationale: "多渠道价格不一致是最常见的对外事故，且会被截图传播。"
      basis:
        - source: kb-brand-guideline
          item: pricing-disclosure-2-1
          path: 品牌规范/2.1 价格披露
          quote: "所有对外物料的价格必须与当期官方指导价一致"
          required: true
      after: aggregate
      check:
        type: rule
        fn: composite.cross_check
        params:
          extractors:
            video_cut: media.extract_price_from_transcript
            article: text.extract_price
            legal_report: record.get_field:contract_price
          tolerance: 0
        assert:
          - field: consistent
            op: "=="
            value: true
            message: "各交付物价格不一致，请核对后重新生成"
      severity: veto          # 对外物料，价格不一致属于红线
```

注意 `severity: veto`：价格/资质/法务口径这类跨产物不一致，应当直接阻断，不能只 warn。这也是父层判据唯一有权用 veto 的典型场景。

## 5. 失败传播

这是复合任务最容易做错的地方。必须显式定义，不能留给运行时发挥。

| 子任务结果 | 默认行为 | 可配置 | 理由 |
|---|---|---|---|
| `veto` | **上浮为父 veto**，父 run `aborted` | 否（强制） | 子任务出现法定否决，父任务不可能"通过"。给开关一定会被滥用 |
| `block` 且子验收未过 | 按父 `on_fail` 处理 | `on_fail` | 父可决定是否降级为 warn |
| `warn` | 记录在父报告，不上浮 | 否 | 20 个子任务的 warn 会淹没父报告，等于没有告警 |
| `failed`（执行失败/超时） | 按父 `on_fail` | `on_fail` + `on_timeout` | 与 block 区分：这是"没跑成"，不是"跑完不合格" |
| 全部通过但父汇总判据 fail | 父 `block` | `policy` | 汇总层是最后一道闸 |

`on_fail` 三档语义：

| 取值 | 行为 | 适用 |
|---|---|---|
| `block`（默认） | 父 run 阻断，等人处理 | 子任务是交付的必需部分 |
| `warn` | 父继续，报告标黄，最终交付物缺该 part | 子任务是增强项（如"额外生成一版花絮"） |
| `continue` | 父继续，该 part 标记 `skipped` | 子任务可完全缺席（如"可选的法务预审"） |

`warn` / `continue` 时，`artifact.parts` 中对应槽位必须允许缺失。这要求 `parts[].required: false`，否则完整性子判据会立刻 fail——这算是配置陷阱，编译期应当报警（V-COMP-02）。

## 6. 执行语义

### 6.1 父子是 run 之间的关系

```
parent_run(run_id=P)
├── subtask_run(subtask=video,   run_id=C1, parent_run_id=P)
├── subtask_run(subtask=copy,    run_id=C2, parent_run_id=P)
└── subtask_run(subtask=legal,   run_id=C3, parent_run_id=P)
```

- 每个子任务是一个**独立 run**，有自己的状态机、事件流、gate、报告。
- 父 run 不执行子包内部任何 step，只等待子 run 终态。
- 存储上 `runs.parent_run_id` 自引用，一层即可（最多两层深度）。

### 6.2 并发与调度

- 无 `depends_on` 的子任务默认**并行**执行（这与单包内 step 默认串行不同，因为子任务之间无产物依赖）。
- 并发上限 `policy.max_parallel`（默认 4），避免一次扇出 50 个子 run 打爆模型配额。
- 每个子任务可单独指定 `model_gateway` 与 `timeout_min`。

### 6.3 重跑与缓存

子 run 的缓存键：`sha256(pack_id@version + inputs_json + knowledge_pin_set)`。

- 命中缓存则直接复用终态与产物，不重跑。
- 因此"改视频判据、重跑父任务"只会重跑 `video` 子 run，`copy` 与 `legal` 直接复用。
- 缓存可显式失效：`hpx run ... --no-cache --subtask video`。

这条是复合任务最大的工程收益：**审片改一版不用重跑整条业务**。没有它，复合任务的实际使用成本会比拆开手工跑还高。

### 6.4 部分重跑与增量

`hpx rerun P --only video,copy` 重跑指定子任务并重新聚合。聚合时未重跑的子任务沿用缓存产物，因此汇总判据（尤其跨产物一致性）会被重新求值——这正是需要的：改了视频价格，一致性判据必须重新判。

## 7. 边界：不支持什么

明确排除，比含糊支持更有用：

| 不支持 | 原因 | 替代方案 |
|---|---|---|
| 三层及以上嵌套 | 失败传播与成本核算会变得无法推理；IR 体积指数增长 | 拆成两个父包，用 `depends_on` 串 |
| 循环 / 迭代直到满足条件 | 这是 BPMN 领域；无终止性保证，无法预估成本 | 反复 `hpx rerun`，由人判断何时停 |
| 子任务之间双向通信 / 协商 | 会破坏"黑盒 + 契约"，退化为多 agent 自由对话（最难调试的形态） | 通过父包 `inputs` 显式传递 |
| 运行时动态决定拆几个子任务 | 无法静态校验产物槽位与汇总判据 | `foreach` 覆盖已知枚举的批量场景 |
| 子包内部再声明 `subtasks` | 同上，且子包作者无法预知自己被谁复用 | 提升为父包的一级子任务 |

`foreach` 的边界也要说清：它只支持**已知枚举**（`inputs.platforms` 是个列表），不支持"边跑边发现要处理哪些"。后者属于 agent 自主规划，不是本框架的职责。

## 8. 校验规则

| 编号 | 规则 | 级别 |
|---|---|---|
| V-COMP-01 | 每个 `subtasks[].produces` 必须有对应 `artifact.parts[].slot`，反之每个 `required: true` 的 part 必须有子任务产出 | error |
| V-COMP-02 | `on_fail` 为 `warn`/`continue` 的子任务，对应 part 必须 `required: false` | error |
| V-COMP-03 | 子任务 `inputs` 必须能填满子包 `input` 槽位的全部必填判据，缺失即编译失败 | error |
| V-COMP-04 | 被引用的 pack 不得含 `subtasks` 块（禁止三层） | error |
| V-COMP-05 | 生产包（`metadata.env: prod`）禁止省略 `pack` 版本号 | error |
| V-COMP-06 | `depends_on` 不得成环；引用不存在的子任务 id 报错并指出位置 | error |
| V-COMP-07 | `foreach` 必须能静态求值（来自 `run.inputs` 或常量），不允许引用其他子任务的产物 | error |

**V-COMP-03 是整套设计的核心约束**：它在编译期就检查"父给的输入能不能喂饱子任务"，而不是运行时让子 agent"理解一下"。跨层信息丢失是多 agent 系统最常见的失败模式，唯一可靠的解法是在编译期用 schema 拦住。

## 9. 什么时候不该拆

拆分有真实代价：跨层丢上下文、多一次调度、多一份审计记录、多一个 gate 要等人。判据只有两条，**都满足才拆**：

1. **子任务有自己的验收标准**——不是"读一个文件"这种纯动作，而是"产出一个需要被独立判定的交付物"。
2. **能被不同父任务复用**——比如"法务审查"既服务于上市内容包，也服务于投标材料包。

只满足第 1 条不满足第 2 条 → 它应该是同一个包里的 step，不是 subtask。
两条都不满足 → 它连 step 都不该单独存在。

反例：把"转写"拆成子任务。转写没有独立验收标准（它的好坏体现在字幕判据里），也不会被别的业务复用——它是 `video` 包里的一个 step。拆出去只会让父包多一层等待。

## 10. 落地顺序

| 阶段 | 做什么 | 备注 |
|---|---|---|
| **S2** | IR 支持 `kind: composite` 与 `subtasks` 展开（单层，子 run 串行） | 图编排本来就在 S2，增量不大 |
| **S2 后半** | 子 run 并行 + 缓存键 + `--only` 重跑 | 缓存是复用的前提，与并行同时做 |
| **S3** | 汇总判据（完整性 / 全局指标）、`parts.*` 上下文 | 一致性判据的抽取函数可后补 |
| **S6** | `foreach` 扇出 + 部分失败隔离 + `collect` 三种聚合 | 批量场景通常在有真实客户后才出现 |
| **S7+** | 一致性判据的行业抽取函数库（价格、参数、条款编号） | 这是行业资产，不是内核功能 |

不建议一上来就做 `foreach`：它看起来简单，但并发控制、部分失败、聚合语义三件事会吃掉大量时间，而单层复合（B 形态）已经覆盖大多数真实业务。

## 11. 待定问题

1. **子包的 `input` 槽位必填判据，如何机器判定"能否填满"？** 现在 `input` 槽位是运行前判据（`rule` + `fn: input.get`），没有显式声明"必填字段清单"。要支持 V-COMP-03，需要给 `input` 判据增加 `requires_inputs: [key]` 声明，或从 `fn: input.get` 的 `params.key` 推断。后者更省事但脆弱，建议显式声明。
2. **父包的 gate 与子包的 gate 会不会重复审批？** 现在可能两级都要求"法务负责人签署"。建议：父包聚合时不重复挂同类 gate，编译期检测同名 `approver_role` + 同一 part 的重复并告警。
3. **跨产物一致性判据的 `tolerance` 语义**：数值容差（±1%）还是精确相等？价格必须精确，参数可容差，需要按字段配置而非全局配置。
4. **子包升级后，历史父 run 的可复现性**：父 run 记录了子包实际版本，但若子包被下架（retired），重跑该怎么办？建议：子包版本永久保留，不允许物理删除。
