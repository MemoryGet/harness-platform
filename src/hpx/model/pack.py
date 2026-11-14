from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"
    VETO = "veto"


class Assertion(StrictModel):
    field: str
    op: Literal["==", "!=", "<", "<=", ">", ">=", "within", "in", "not_in", "contains", "matches", "min_count"]
    value: object
    message: str | None = None


class RuleChecker(StrictModel):
    type: Literal["rule"]
    fn: str
    params: dict = Field(default_factory=dict)
    assertions: list[Assertion] = Field(alias="assert", min_length=1)


class SchemaChecker(StrictModel):
    type: Literal["schema"]
    schema_ref: str
    strict: bool = True


class JudgeChecker(StrictModel):
    type: Literal["judge"]
    mode: Literal["absolute", "pairwise"] = "absolute"
    model: str = "judge-default"
    rubric: list[str]
    scale: str = "1-5"
    pass_if: str
    samples: int = Field(default=3, ge=1)
    reference: str | None = None
    grounding: dict | None = None


class GateChecker(StrictModel):
    type: Literal["gate"]
    approver_role: str
    timeout_min: int = 30
    on_timeout: Literal["escalate", "auto_reject"] = "escalate"
    must_see: list[str] = Field(default_factory=list)
    prompt: str = ""


Checker = Annotated[RuleChecker | SchemaChecker | JudgeChecker | GateChecker, Field(discriminator="type")]


class Criterion(StrictModel):
    id: str
    title: str
    ask: str
    rationale: str | None = None
    evidence: list[str] = Field(default_factory=list)
    basis: list[dict] = Field(default_factory=list)
    applies_when: str | None = None
    after: str
    check: Checker
    severity: Severity


class Metadata(StrictModel):
    id: str
    name: str
    industry: str
    version: str
    owner: str
    description: str | None = None


class ArtifactSpec(StrictModel):
    kind: Literal["text", "timeline", "record", "table", "composite"]
    format: str | None = None
    schema_ref: str | None = None
    parts: list[dict] = Field(default_factory=list)


class Step(StrictModel):
    id: str
    uses: str
    output: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)


class Workflow(StrictModel):
    steps: list[Step]


class Slots(StrictModel):
    input: list[Criterion] = Field(default_factory=list)
    process: list[Criterion] = Field(default_factory=list)
    quality: list[Criterion] = Field(default_factory=list)
    veto: list[Criterion] = Field(default_factory=list)


class CriterionPack(StrictModel):
    dsl_version: Literal["0.1"]
    kind: Literal["CriterionPack"]
    metadata: Metadata
    artifact: ArtifactSpec
    evidence: list[str] = Field(default_factory=list)
    knowledge: dict | None = None
    subtasks: list[dict] = Field(default_factory=list)
    workflow: Workflow
    slots: Slots
    policy: dict = Field(default_factory=dict)
    eval: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self):
        steps = [step.id for step in self.workflow.steps]
        if len(steps) != len(set(steps)):
            raise ValueError("workflow.steps 包含重复的 id")
        ids = []
        for slot in ("input", "process", "quality", "veto"):
            for criterion in getattr(self.slots, slot):
                ids.append(criterion.id)
                if criterion.after not in steps:
                    raise ValueError(f"判据 {criterion.id} 的 after={criterion.after} 不在 workflow.steps 中")
                if slot == "veto" and isinstance(criterion.check, JudgeChecker):
                    raise ValueError(f"判据 {criterion.id}: judge 不允许用于 veto 槽位")
        if len(ids) != len(set(ids)):
            raise ValueError("slots 包含重复的判据 id")
        return self
