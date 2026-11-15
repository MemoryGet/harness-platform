"""Human approval gate state for single-check debugging."""

from hpx.model.pack import GateChecker


def check_gate(checker: GateChecker) -> dict:
    return {"status": "pending", "approver_role": checker.approver_role,
            "timeout_min": checker.timeout_min, "on_timeout": checker.on_timeout,
            "must_see": checker.must_see, "prompt": checker.prompt}
