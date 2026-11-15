"""Execution orchestration with injected step and checker implementations."""

from typing import Callable

from hpx.dsl.expr import evaluate_expression
from hpx.runtime.store import RunStore


StepExecutor = Callable[[dict, dict], dict]
CheckExecutor = Callable[[dict, dict], dict]


class Runner:
    def __init__(self, store: RunStore, step_executor: StepExecutor, check_executor: CheckExecutor):
        self.store = store
        self.step_executor = step_executor
        self.check_executor = check_executor

    def start(self, tenant_id: str, ir: dict, inputs: dict, *, triggered_by: str) -> str:
        run_id = self.store.create_run(tenant_id, ir, inputs, triggered_by)
        self.advance(tenant_id, run_id)
        return run_id

    def resume(self, tenant_id: str, run_id: str, criterion_id: str,
               decision: str, approver: str, note: str) -> str:
        run = self.store.get_run(tenant_id, run_id)
        if run["status"] != "awaiting_gate":
            raise ValueError("当前 run 未等待人工闸门")
        pending = [event for event in self.store.events(tenant_id, run_id)
                   if event["event_type"] == "gate_pending" and event["payload"]["criterion_id"] == criterion_id]
        if not pending:
            raise ValueError(f"没有待决闸门：{criterion_id}")
        self.store.decide_gate(tenant_id, run_id, criterion_id, approver, decision, note)
        if decision == "reject":
            self.store.append_event(tenant_id, run_id, "run_aborted",
                                    {"criterion_id": criterion_id, "reason": "gate_rejected"}, "aborted")
            return "aborted"
        return self.advance(tenant_id, run_id)

    def advance(self, tenant_id: str, run_id: str) -> str:
        run = self.store.get_run(tenant_id, run_id)
        if run["status"] in ("done", "failed", "aborted"):
            return run["status"]
        events = self.store.events(tenant_id, run_id)
        completed_steps = {event["payload"]["step_id"]: event["payload"]["output"]
                           for event in events if event["event_type"] == "step_done"}
        completed_checks = {event["payload"]["criterion_id"]
                            for event in events if event["event_type"] == "check_result"}
        decisions = {event["payload"]["criterion_id"]: event["payload"]["decision"]
                     for event in events if event["event_type"] == "gate_decided"}
        pending_gates = {event["payload"]["criterion_id"] for event in events
                         if event["event_type"] == "gate_pending"}
        ir = run["ir"]
        evidence = {node.get("output", node["id"]): completed_steps[node["id"]]
                    for node in ir["graph"]["nodes"] if node["id"] in completed_steps}
        checks_by_step = {}
        for check in ir["checkpoints"]:
            checks_by_step.setdefault(check["after"], []).append(check)
        context = {"input": run["inputs"], "artifact": ir["artifact"],
                   "evidence": evidence, "env": {}}
        for node in ir["graph"]["nodes"]:
            step_id = node["id"]
            if step_id not in completed_steps:
                output = self.step_executor(node, context)
                if not isinstance(output, dict):
                    raise TypeError(f"节点 {step_id} 必须返回对象")
                self.store.append_event(tenant_id, run_id, "step_done",
                                        {"step_id": step_id, "output": output})
                completed_steps[step_id] = output
                evidence[node.get("output", step_id)] = output
            for check in checks_by_step.get(step_id, []):
                criterion_id = check["criterion_id"]
                if criterion_id in completed_checks:
                    continue
                condition = check.get("applies_when")
                if condition and not evaluate_expression(condition, context):
                    result = {"status": "skipped", "reason": "applies_when 为假"}
                elif check["checker"]["type"] == "gate":
                    if criterion_id not in decisions:
                        if criterion_id not in pending_gates:
                            self.store.append_event(tenant_id, run_id, "gate_pending",
                                                    {"criterion_id": criterion_id, "step_id": step_id}, "awaiting_gate")
                        return "awaiting_gate"
                    result = {"status": "pass" if decisions[criterion_id] == "approve" else "fail"}
                else:
                    result = self.check_executor(check, context)
                self.store.append_event(tenant_id, run_id, "check_result",
                                        {"criterion_id": criterion_id, "step_id": step_id,
                                         "status": result["status"], "result": result})
                completed_checks.add(criterion_id)
                if result["status"] == "error":
                    self.store.append_event(tenant_id, run_id, "run_failed",
                                            {"criterion_id": criterion_id, "reason": "checker_error"}, "failed")
                    return "failed"
                if result["status"] == "fail" and check["severity"] == "veto":
                    self.store.append_event(tenant_id, run_id, "run_aborted",
                                            {"criterion_id": criterion_id, "reason": "veto"}, "aborted")
                    return "aborted"
                if result["status"] == "fail" and check["severity"] == "block":
                    self.store.append_event(tenant_id, run_id, "run_failed",
                                            {"criterion_id": criterion_id, "reason": "block"}, "failed")
                    return "failed"
        self.store.append_event(tenant_id, run_id, "run_done", {}, "done")
        return "done"
