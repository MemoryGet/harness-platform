# Harness Platform —— 判据驱动的业务 Agent 构建框架

## 当前开发进度（S1 起步）

已实现可安装的 Python 包、四份样例的结构校验，以及基于采集结果 JSON 的单条 `rule` 断言调试。真实采集器、知识引用校验、`judge`、编译与运行时仍在开发中。

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/hpx validate examples/video-shortform.yaml examples/legal-contract-review.yaml examples/support-ticket-triage.yaml
.venv/bin/python -m unittest discover -s tests
```

`hpx check` 当前需要已有采集结果，例如：

```bash
echo '{"audio":{"integrated_lufs":-13.6}}' > /tmp/hpx-evidence.json
.venv/bin/hpx check --pack examples/video-shortform.yaml --criterion loudness_target --evidence /tmp/hpx-evidence.json
```

> 一句话：把业务专家脑子里的**隐性判据**，变成可执行、可验收、可回滚的 agent。

本仓库的业务验证仍处于 **S0 阶段（设计定稿 / 纸面验证）**，同时已开始 S1 的基础代码。文档包的用途是：在投入 UI 与完整实现之前，先把**行业无关的内核**定义清楚，并用三份跨行业判据样例做纸面压测。

## 为什么是"判据优先"，而不是"流程优先"

市面上已有 Dify / Coze / n8n / LangGraph 等成熟工具，它们的共性是**帮你把流程画出来**。但流程跑通 ≠ 专业。专业与业余的差别在于：

- 什么算合格交付？什么必须打回？
- 什么情况必须升级人工？什么必须拒答？

这些是判据，不是流程。现有平台给的是一张空白画布加一个提示词框，业务团队填完仍然做不出专业级 agent。本框架的赌注是：**判据可资产化，且这是唯一可沉淀的护城河。**

## 文档导航

| 文档 | 内容 | 读者 |
|---|---|---|
| [docs/01-roadmap.md](docs/01-roadmap.md) | S0–S7 阶段划分、每阶段任务、Gate 退出条件 | 全员 / 决策 |
| [docs/02-architecture.md](docs/02-architecture.md) | 三分离架构、必须现在定的五件事、存储模型 | 架构 / 后端 |
| [docs/03-dsl-spec.md](docs/03-dsl-spec.md) | 判据 DSL 规范：四槽位、四类检查器、严重级语义 | 全员必读 |
| [docs/04-core-skeleton.md](docs/04-core-skeleton.md) | 无头内核目录结构、接口签名、CLI 命令 | 后端 |
| [docs/05-decisions.md](docs/05-decisions.md) | 关键决策记录与待定问题 | 全员 |
| [docs/06-knowledge.md](docs/06-knowledge.md) | 知识库绑定：三种用途、快照锁定、引用闭环、11 条校验规则 | 后端 / 判据设计 |
| [docs/07-knowledge-integration.md](docs/07-knowledge-integration.md) | 外部知识库接入：MCP 放哪层、Provider 接口、同步与权限 | 架构 / 后端 |
| [docs/08-composition.md](docs/08-composition.md) | 复合任务：子任务组合、汇总判据、失败传播、不支持清单 | 架构 / 判据设计 |
| [examples/](examples/) | 四份判据 YAML 样例（含一份复合任务） | 全员必读 |
| [knowledge/](knowledge/) | 知识源样例（法条 + 内部指引 + 案例库） | 判据设计 |

## 样例就是压测用例

| 样例 | 行业 | 产物类型 | 判据特点 |
|---|---|---|---|
| `video-shortform.yaml` | 短视频批量出片 | `timeline` (OTIO) | 技术指标可完全机检（响度、字幕、规格、AI 披露） |
| `legal-contract-review.yaml` | 合同条款审查 | `text` (条款树) | 部分机检 + 大量模型评审 + 强制人工闸门 |
| `support-ticket-triage.yaml` | 客服工单分派 | `record` (JSON) | 结构化输出可断言 + SLA 时限约束 |
| `composite/launch-content-package.yaml` | 新车上市内容包 | `composite`（timeline + text + record） | 3 个子任务组合 + 跨产物一致性 + 法定否决 |

**压测通过标准（S4 Gate）**：前三份 YAML 在内核代码**零修改**的前提下全部可以 validate + compile + run，且每份至少有 3 条检查器真实执行。若某份需要改内核 schema，说明抽象错误——现在改成本最低。

第四份是复合任务样例，不参与 S4 的三行业判定，它是 **S2 复合能力**的验收对象：验证"父包只声明契约、子包黑盒、汇总判据跨产物"这条路走得通。

## 三条不可违背的架构铁律

1. **DSL 是唯一真相。** 画布只是 DSL 的一个编辑器，绝不能把画布 JSON 当存储真相。否则 UI 就成了内核，换行业必须重写。
2. **内核必须无头。** 删掉所有 UI 后，CLI + YAML 必须能跑通全流程。做不到就是架构错。
3. **执行壳不自研。** 工具循环、沙箱、会话恢复用现成的（DeepSeek Harness / Claude Agent SDK），本框架只做判据层、编译层、验收层。

## 知识库怎么接：不是 RAG，是依据层

框架对知识库只有一个要求：**每条硬判据都能回答"凭什么"。** 于是知识库有三种用途，只有两种能进判定：

| 用途 | 位置 | 参与判定 | 检索方式 |
|---|---|---|---|
| 判据依据 | `criterion.basis` | 是 | 精确（item_id / path） |
| 检查依据 | `check.grounding` | 是 | 精确（限定源 + 条目集） |
| 生成上下文 | `step.config.retrieval` | 否 | 语义（向量召回） |

推论（第三条铁律的延伸）：**判定链路上只允许精确检索**。向量召回会因索引重建、切分调整、模型升级而变，一旦进入判定，同一份输入在不同时间会得到不同结论，`eval` 与审计同时失效。

详见 [docs/06-knowledge.md](docs/06-knowledge.md)，样例见 [knowledge/legal-cn.yaml](knowledge/legal-cn.yaml)。

用户自带的知识库（文档目录 / MCP 服务）走 [docs/07-knowledge-integration.md](docs/07-knowledge-integration.md)：接入收敛在平台侧连接器注册表，**入库通道物化快照供内核判定**——判定链路不直连外部服务。执行壳侧的接入暂缓，S2 之后再定。

## 快速了解一个判据长什么样

```yaml
slots:
  quality:
    - id: loudness_target
      title: 响度达标
      ask: "成片响度应该是多少 LUFS？容差多少？"
      rationale: "各平台响度标准不同，超标会被限流。"
      check:
        type: rule
        fn: media.loudness
        params: { target_lufs: -14, tolerance: 1.0 }
      severity: block
```

业务人员在表单里回答 `ask`，系统把它编译成 `check`，验收时逐条判定 `pass / fail`。这就是整个产品的核心循环。

## 当前仓库内容

```
harness-platform/
├── README.md                       本文件
├── docs/
│   ├── 01-roadmap.md               S0–S7 路线图（含 Gate 退出条件）
│   ├── 02-architecture.md          三分离、五件必定义、存储模型
│   ├── 03-dsl-spec.md              DSL v0.1 规范（四槽位 + 四类检查器 + IR + 知识绑定）
│   ├── 04-core-skeleton.md         内核目录结构、接口签名、CLI 命令
│   ├── 05-decisions.md             ADR 决策记录（D-001~D-011）+ 16 个待定问题
│   ├── 06-knowledge.md             知识库绑定：三种用途、快照锁定、引用闭环
│   ├── 07-knowledge-integration.md 外部知识库接入：MCP 位置、Provider、同步与权限
│   └── 08-composition.md           复合任务：子任务组合、汇总判据、失败传播
├── examples/
│   ├── video-shortform.yaml        样例 1 · timeline · 18 条判据
│   ├── legal-contract-review.yaml  样例 2 · text · 14 条判据（3 条挂知识依据）
│   ├── support-ticket-triage.yaml  样例 3 · record · 15 条判据
│   └── composite/
│       └── launch-content-package.yaml  样例 4 · composite · 3 子任务 8 判据
├── knowledge/
│   ├── README.md                   知识源格式说明与演示点
│   ├── legal-cn.yaml               法条 + 内部指引 + 案例库（7 条，含已废止条款）
│   └── brand-ad.yaml               品牌规范 + 广告法（3 条，供复合样例使用）
├── golden/
│   ├── README.md                   黄金集构建规范
│   └── video.yaml                  7 条 case（legal / support 待 S0 补齐）
└── schemas/
    ├── clause_tree.json            条款树结构（legal 用）
    └── triage_result.json          分派结果结构（support 用）
```

**下一步动作**：按 [docs/01-roadmap.md](docs/01-roadmap.md) 的 S0 执行——找三位业务专家访谈，用 `examples/` 里的三份 YAML 做对照修改，而不是从零写。

