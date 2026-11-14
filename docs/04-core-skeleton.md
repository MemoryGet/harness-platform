# 无头内核骨架

> 目标：`pip install -e .` 之后，仅用 CLI + YAML 就能跑通 validate / compile / run / eval / gate 全流程。**不含任何 UI 代码。**

## 1. 目录结构

```
harness-platform/
├── pyproject.toml
├── README.md
├── docs/
├── examples/                      # 三份行业判据样例（S4 压测用例）
│   ├── video-shortform.yaml
│   ├── legal-contract-review.yaml
│   └── support-ticket-triage.yaml
├── knowledge/                     # 知识源（判据依据，S1 起）
│   └── legal-cn.yaml
├── golden/                        # 黄金集（S3 起）
│   ├── video.yaml
│   ├── legal.yaml
│   └── support.yaml
├── schemas/                       # JSON Schema（产物结构）
│   └── shortform_timeline.json
└── src/hpx/
    ├── __init__.py
    ├── model/                     # ① 数据模型（Pydantic v2）
    │   ├── pack.py                # CriterionPack / Metadata / ArtifactSpec
    │   ├── criterion.py           # Criterion / Severity
    │   ├── checker.py             # 四类 Checker 的判别联合
    │   ├── result.py              # CheckResult / RunReport
    │   ├── ir.py                  # IR 模型（编译产物）
    │   └── composite.py           # CompositeSpec / Part / Subtask（复合任务）
    ├── dsl/                       # ② 解析与校验
    │   ├── loader.py              # YAML → 模型，含业务化错误信息
    │   ├── errors.py              # DslError(pack_id, path, code, message, hint)
    │   └── expr.py                # applies_when 表达式解析与求值
    ├── compile/                   # ③ 编译 DSL → IR
    │   ├── compiler.py
    │   ├── subtasks.py            # pack 版本解析 + 输入契约匹配（V-COMP-01~07）
    │   └── topo.py                # 拓扑排序
    ├── knowledge/                 # ③b 知识绑定（依据层，S1 起）
    │   ├── source.py              # 加载 KnowledgeSource、计算 checksum
    │   ├── snapshot.py            # 打快照、pin 集合、生效区间判定
    │   ├── binder.py              # basis → item_id 解析（编译期，失败即编译失败）
    │   ├── citations.py           # 引用闭环校验：存在 / 生效 / 原文 / 权威级
    │   └── providers/             # 外部源接入适配器（判定侧只暴露 resolve）
    │       ├── base.py            # Protocol: describe / sync / resolve（故意无 search）
    │       ├── filesystem.py      # S1：本地文档目录
    │       ├── mcp.py             # S2：锚点映射表 + 全文 fetch
    │       └── object_storage.py  # S6：S3 / OSS / 网盘
    ├── checkers/                  # ④ 四类检查器
    │   ├── base.py                # Protocol + CheckContext
    │   ├── registry.py            # 名字 → 实现 的注册表
    │   ├── rule.py
    │   ├── schema.py
    │   ├── judge.py
    │   ├── gate.py
    │   └── builtin/               # 采集函数（行业相关，插件式）
    │       ├── media.py           # media.probe / media.loudness（ffprobe）
    │       ├── otio_ops.py        # otio.validate / otio.caption_metrics
    │       ├── text_ops.py        # text.clause_tree / text.citation_check
    │       └── record_ops.py      # record.field_check / record.sla
    ├── runtime/                   # ⑤ 执行器
    │   ├── runner.py
    │   ├── state.py               # 状态机 + 事件溯源
    │   ├── subrun.py              # 子 run 调度：并发上限、缓存键、--only 重跑
    │   ├── tool_gateway.py        # 壳侧工具收口：白名单 + 注入 pin + 审计留痕
    │   └── store.py               # 存储抽象（SQLite / Postgres）
    ├── evaluate/                  # ⑥ 验收
    │   ├── golden.py
    │   ├── evaluator.py
    │   └── gate.py                # 发布门禁判定
    ├── loaders/                   # ⑦ 产物加载器（按 kind 分派）
    │   ├── base.py
    │   ├── text.py
    │   ├── timeline.py            # OTIO
    │   └── record.py
    └── cli/
        └── main.py                # Typer
```

**分层依赖规则（用 import-linter 强制）**：
`cli → evaluate → runtime → checkers → compile → dsl → model`，不允许反向依赖。
`checkers/builtin/*` 不许被 `model` / `dsl` 引用——行业逻辑必须可拔插。

## 2. 核心接口

### 2.1 数据模型

```python
# model/criterion.py
from enum import StrEnum

class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"
    VETO = "veto"

class Slot(StrEnum):
    INPUT = "input"
    PROCESS = "process"
    QUALITY = "quality"
    VETO = "veto"

class Criterion(BaseModel):
    id: str
    title: str
    ask: str
    rationale: str | None = None
    evidence: list[str] = []
    applies_when: str | None = None       # 表达式原文，编译期解析
    after: str                            # step_id
    check: Checker                        # 判别联合，见下
    severity: Severity
```

```python
# model/checker.py  —— 判别联合，靠 type 字段分派
class RuleChecker(BaseModel):
    type: Literal["rule"] = "rule"
    fn: str
    params: dict = {}
    assert_: list[Assertion] = Field(alias="assert")

class SchemaChecker(BaseModel):
    type: Literal["schema"] = "schema"
    schema_ref: str
    strict: bool = True

class JudgeChecker(BaseModel):
    type: Literal["judge"] = "judge"
    mode: Literal["absolute", "pairwise"] = "absolute"
    model: str = "judge-default"
    rubric: list[str]
    scale: str = "1-5"
    pass_if: str                          # 例如 "score >= 4"
    samples: int = 3
    reference: str | None = None

class GateChecker(BaseModel):
    type: Literal["gate"] = "gate"
    approver_role: str
    timeout_min: int = 30
    on_timeout: Literal["escalate", "auto_reject"] = "escalate"
    must_see: list[str] = []
    prompt: str = ""

Checker = Annotated[
    RuleChecker | SchemaChecker | JudgeChecker | GateChecker,
    Field(discriminator="type"),
]
```

```python
# model/result.py
class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"          # 采集/执行本身出错，区别于判定失败
    SKIPPED = "skipped"      # applies_when 为假

class CheckResult(BaseModel):
    criterion_id: str
    slot: Slot
    status: CheckStatus
    severity: Severity
    message: str
    assertions: list[dict] = []       # 每条 assert 的 field/actual/expected/ok
    evidence: dict = {}
    duration_ms: int = 0
    model_version: str | None = None  # judge 专用
    raw_scores: list[float] = []      # judge 多次采样
```

### 2.2 检查器协议与注册表

```python
# checkers/base.py
class CheckContext(BaseModel):
    tenant_id: str
    run_id: str
    step_id: str
    inputs: dict
    evidence: dict          # 已收集的证据，按名索引
    artifact: ArtifactRef   # kind / format / uri / meta
    pack: PackSnapshot

class CheckerImpl(Protocol):
    type: Literal["rule", "schema", "judge", "gate"]
    def run(self, criterion: Criterion, ctx: CheckContext) -> CheckResult: ...
```

```python
# checkers/registry.py
_RULE_FNS: dict[str, Callable[[CheckContext, dict], dict]] = {}
_JUDGES: dict[str, "JudgeBackend"] = {}

def rule_fn(name: str):
    """注册采集函数。fn 只采集证据，不做判定。"""
    def deco(f): _RULE_FNS[name] = f; return f
    return deco

def get_rule_fn(name: str):
    if name not in _RULE_FNS:
        raise DslError(code="UNKNOWN_FN",
                       message=f"找不到采集函数「{name}」",
                       hint=f"已注册：{sorted(_RULE_FNS)}")
    return _RULE_FNS[name]
```

采集函数契约（**这是最关键的边界**）：

```python
@rule_fn("media.probe")
def media_probe(ctx: CheckContext, params: dict) -> dict:
    """返回扁平/嵌套 JSON，供 assert 用 field 路径取值。不许在这里做 pass/fail 判定。"""
    return {
        "audio": {"integrated_lufs": -13.6, "true_peak_db": -1.2},
        "video": {"duration_sec": 42.5, "width": 1080, "height": 1920, "fps": 30},
        "black_frames": 0,
    }
```

### 2.3 编译器

```python
# compile/compiler.py
class Compiler:
    def compile(self, pack: CriterionPack) -> IR:
        """纯函数：pack → IR。幂等，字段排序稳定，不含时间戳。"""
```

IR 里必须包含：`graph`（节点与边）、`checkpoints`（判据挂载点，已按与拓扑序一致的顺序排好）、`gates`、`eval`、`pack.checksum`。

`checksum = sha256(规范化后的 YAML 文本)`，用于判断"运行时用的到底是哪版判据"。

### 2.4 运行时

```python
# runtime/runner.py
class Runner:
    def __init__(self, store: RunStore, registry: CheckerRegistry, loaders: LoaderRegistry): ...

    def start(self, ir: IR, inputs: dict, *, triggered_by: str) -> RunHandle:
        """创建 run，从 DAG 起点开始推进，直到 done / failed / awaiting_gate / aborted。"""

    def resume(self, run_id: str, decision: GateDecision) -> RunHandle:
        """人工闸门放行/驳回后继续。幂等：重复 resume 同一 run 不重复执行已完成节点。"""

    def status(self, run_id: str) -> RunStatus: ...
```

执行规则：

1. 节点执行前检查 `run_events`——已完成的节点直接跳过（恢复幂等）。
2. 节点产出后，依次跑挂载的检查点，顺序 `veto → block → warn → info`。
3. 出现 `veto` → 立即 `aborted`，写事件，不再执行后续节点。
4. 出现 `gate` → 状态转 `awaiting_gate`，写 `gate_decision` 待办，返回。
5. `block` 失败 → 按 `policy.on_fail` 处理；`retry` 计数用 `run_events` 里的 attempt。
6. 每个事件都带 `seq`，单调递增。

### 2.5 验收与门禁

```python
# evaluate/evaluator.py
class Evaluator:
    def run_golden(self, ir: IR, cases: list[GoldenCase]) -> EvalReport: ...
    def compare(self, base: EvalReport, head: EvalReport) -> EvalDiff: ...

# evaluate/gate.py
class PublishGate:
    def decide(self, report: EvalReport, policy: EvalPolicy) -> GateDecision:
        """min_pass_rate / max_veto 不满足 → 拒绝发布，并列出失败 case。"""
```

`EvalReport` 必含：总通过率、按判据维度的失败分布、按 severity 的阻断数、耗时、**判据覆盖率**（多少条判据被至少一条 case 命中——没人命中的判据等于没验证过）。

## 3. CLI 命令

```bash
hpx validate examples/video-shortform.yaml            # 业务化报错
hpx compile  examples/video-shortform.yaml -o ir.json  # 幂等
hpx run      examples/video-shortform.yaml --input ./in --dry-run
hpx run      examples/video-shortform.yaml --input ./in --pause-on-gate
hpx resume   <run_id> --decision approve --note "已确认"
hpx check    --criterion loudness_target --artifact demo.mp4   # 单条调试
hpx eval     examples/video-shortform.yaml --set golden/video.yaml
hpx eval     --compare 1.0.0..1.1.0
hpx gate     --report eval-report.json --min-pass 0.9 --max-veto 0
hpx migrate  examples/ --to 0.2                        # DSL 升级
```

退出码约定：`0` 成功 / `1` 校验或验收失败 / `2` 参数错误 / `3` 运行时错误。CI 直接依赖退出码。

## 4. 最小可运行顺序（S1 第一天的 recommended path）

1. `model/` 五个文件写全，能 `CriterionPack.model_validate(yaml.safe_load(...))`。
2. `dsl/loader.py` 包一层，把 Pydantic 错误翻译成 `DslError`。
3. `checkers/rule.py` + `builtin/media.py` 的一条 `media.probe`。
4. CLI 只做 `validate` 和 `check` 两个命令。
5. 写一条测试：给一个假 probe 结果，断言 `within [-15, -13]` 对 `-13.6` 通过、对 `-11` 失败。

这五步能跑通，S1 的技术风险就清空了。

## 5. 三条实现纪律

1. **采集与判定分离**：`fn` 只返回数据，`assert` 只做比较。业务人员改的是 `assert`，工程师改的是 `fn`——这条分界线决定了 S5 表单能不能成立。
2. **内核不含行业词汇**：`model/` 与 `compile/` 里不允许出现 `loudness` / `clause` / `ticket` 这类词。出现即泄露，S4 压测必然失败。
3. **所有判定必须留证据**：`CheckResult.evidence` 不许为空（`gate` 除外）。这是"可审计"的地基，也是 S6 行业视图的数据来源。
