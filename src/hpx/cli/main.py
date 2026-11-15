import json
from datetime import date
from pathlib import Path

import typer

from hpx.checkers.rule import check_rule
from hpx.checkers.judge import JudgeService, ModelTarget
from hpx.checkers.registry import get_rule_fn
from hpx.checkers.schema import check_schema
from hpx.checkers.gate import check_gate
from hpx.dsl.loader import DslError, load_pack
from hpx.knowledge.validate import validate_basis
from hpx.model.pack import GateChecker, JudgeChecker, RuleChecker, SchemaChecker
from hpx.compile import compile_pack
from hpx.runtime.builtin import execute_check, execute_step
from hpx.runtime.runner import Runner
from hpx.runtime.store import RunStore

app = typer.Typer(help="判据包验证与单条判据调试")


def knowledge_directory(path: Path) -> Path:
    for root in (path.resolve().parent, *path.resolve().parents):
        candidate = root / "knowledge"
        if candidate.is_dir():
            return candidate
    return path.resolve().parent / "knowledge"


def schema_path(pack: Path, reference: str) -> Path:
    for root in (pack.resolve().parent, *pack.resolve().parents):
        candidate = (root / reference).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    raise ValueError(f"找不到 Schema 文件：{reference}")


def pack_catalog(pack_path: Path) -> dict:
    examples = knowledge_directory(pack_path).parent / "examples"
    catalog = {}
    if examples.is_dir():
        for path in examples.rglob("*.yaml"):
            candidate = load_pack(path)
            if candidate.artifact.kind != "composite":
                catalog.setdefault(candidate.metadata.id, []).append(candidate)
    return catalog


@app.command()
def validate(paths: list[Path] = typer.Argument(..., help="一个或多个判据包 YAML"),
             publish_date: str | None = typer.Option(None, help="包发布日期 YYYY-MM-DD，用于 V-KB-11"),
             publish_ready: bool = typer.Option(False, "--publish-ready", help="额外检查发布依据覆盖率")) -> None:
    try:
        date_value = date.fromisoformat(publish_date) if publish_date else None
    except ValueError as exc:
        typer.echo(f"无效发布日期：{publish_date}", err=True)
        raise typer.Exit(2) from exc
    failed = False
    for path in paths:
        try:
            pack = load_pack(path)
            validate_basis(pack, knowledge_directory(path), date_value, publish_ready)
            typer.echo(f"通过 {path} ({pack.metadata.id})")
        except DslError as exc:
            typer.echo(f"失败 {exc}", err=True)
            failed = True
    if failed:
        raise typer.Exit(1)


@app.command("compile")
def compile_command(pack: Path = typer.Argument(..., help="判据包 YAML"),
                    output: Path | None = typer.Option(None, "-o", "--output", help="IR JSON 输出文件")) -> None:
    try:
        ir = compile_pack(load_pack(pack), knowledge_directory(pack), pack_catalog(pack))
        serialized = json.dumps(ir, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if output:
            output.write_text(serialized, encoding="utf-8")
        else:
            typer.echo(serialized, nl=False)
    except (DslError, OSError, ValueError) as exc:
        typer.echo(f"编译失败：{exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def run(pack: Path = typer.Argument(..., help="判据包 YAML"),
        dry_run: bool = typer.Option(False, "--dry-run", help="仅打印执行图，不执行节点"),
        input_file: Path | None = typer.Option(None, "--input", help="运行输入 JSON 文件"),
        state: Path = typer.Option(Path("/workspace/.hpx/runs.sqlite3"), help="SQLite 运行日志路径"),
        tenant: str = typer.Option("local", help="租户标识")) -> None:
    try:
        ir = compile_pack(load_pack(pack), knowledge_directory(pack), pack_catalog(pack))
    except (DslError, OSError, ValueError) as exc:
        typer.echo(f"无法生成执行图：{exc}", err=True)
        raise typer.Exit(1) from exc
    if not dry_run:
        if input_file is None:
            typer.echo("实际运行需要 --input JSON 文件", err=True)
            raise typer.Exit(2)
        try:
            inputs = json.loads(input_file.read_text(encoding="utf-8"))
            if not isinstance(inputs, dict):
                raise ValueError("运行输入必须是 JSON 对象")
            store = RunStore(state)
            run_id = Runner(store, execute_step, execute_check).start(tenant, ir, inputs, triggered_by="cli")
            status = store.get_run(tenant, run_id)["status"]
            typer.echo(json.dumps({"run_id": run_id, "status": status}, ensure_ascii=False))
            if status != "done":
                raise typer.Exit(1)
            return
        except typer.Exit:
            raise
        except (OSError, ValueError, RuntimeError, NotImplementedError) as exc:
            typer.echo(f"运行未完成：{exc}", err=True)
            raise typer.Exit(3) from exc
    checks = {}
    for subtask in ir.get("subtasks", []):
        typer.echo(f"subtask {subtask['id']} -> {subtask['pack_id']}@{subtask['pack_version']}")
        typer.echo(f"  requires {', '.join(subtask['required_inputs'])}")
    for checkpoint in ir["checkpoints"]:
        checks.setdefault(checkpoint["after"], []).append(checkpoint)
    for node in ir["graph"]["nodes"]:
        typer.echo(f"{node['id']} ({node['uses']})")
        for checkpoint in checks.get(node["id"], []):
            checker = checkpoint["checker"]
            detail = checker.get("fn", checker.get("schema_ref", checker.get("approver_role", checker.get("model", ""))))
            typer.echo(f"  -> {checkpoint['criterion_id']} [{checkpoint['severity']}] {checker['type']} {detail}")
            if checker["type"] == "rule":
                for assertion in checker["assert"]:
                    typer.echo(f"     {assertion['field']} {assertion['op']} {json.dumps(assertion['value'], ensure_ascii=False)}")
            elif checker["type"] == "judge":
                typer.echo(f"     {checker['pass_if']} (samples={checker['samples']})")
            if checkpoint.get("applies_when"):
                typer.echo(f"     when {checkpoint['applies_when']}")


@app.command()
def resume(run_id: str = typer.Argument(..., help="运行 id"),
           decision: str = typer.Option(..., help="approve 或 reject"),
           note: str = typer.Option(..., help="审批说明"),
           approver: str = typer.Option(..., help="审批人标识"),
           state: Path = typer.Option(Path("/workspace/.hpx/runs.sqlite3"), help="SQLite 运行日志路径"),
           tenant: str = typer.Option("local", help="租户标识")) -> None:
    try:
        store = RunStore(state)
        events = store.events(tenant, run_id)
        decided = {event["payload"]["criterion_id"] for event in events
                   if event["event_type"] == "gate_decided"}
        pending = [event["payload"]["criterion_id"] for event in events
                   if event["event_type"] == "gate_pending" and event["payload"]["criterion_id"] not in decided]
        if len(pending) != 1:
            raise ValueError(f"待决人工闸门数量应为 1，实际为 {len(pending)}")
        status = Runner(store, execute_step, execute_check).resume(
            tenant, run_id, pending[0], decision, approver, note)
        typer.echo(json.dumps({"run_id": run_id, "status": status}, ensure_ascii=False))
        if status not in ("done", "awaiting_gate"):
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except (KeyError, OSError, ValueError, RuntimeError, NotImplementedError) as exc:
        typer.echo(f"恢复失败：{exc}", err=True)
        raise typer.Exit(3) from exc


@app.command()
def check(
    criterion: str = typer.Option(..., help="判据 id"),
    evidence: Path | None = typer.Option(None, help="rule 采集函数输出的 JSON 文件"),
    artifact: Path | None = typer.Option(None, help="rule 的媒体文件或 judge 的 UTF-8 文本文件"),
    models: Path | None = typer.Option(None, help="模型端点配置 JSON；默认 OpenAI"),
    pack: Path = typer.Option(..., help="判据包 YAML"),
) -> None:
    try:
        loaded = load_pack(pack)
        matches = [item for slot in ("input", "process", "quality", "veto")
                   for item in getattr(loaded.slots, slot) if item.id == criterion]
        if not matches:
            typer.echo(f"找不到判据：{criterion}", err=True)
            raise typer.Exit(2)
        item = matches[0]
        if isinstance(item.check, RuleChecker):
            if evidence is not None:
                values = json.loads(evidence.read_text(encoding="utf-8"))
            elif artifact is not None:
                values = get_rule_fn(item.check.fn)(artifact, item.check.params)
            else:
                raise ValueError("该 rule 检查需要 --evidence，或已支持的采集器加 --artifact")
            results = check_rule(item.check, values)
            output = {"criterion_id": criterion, "status": "pass" if all(row["ok"] for row in results)
                      else "fail", "assertions": results}
        elif isinstance(item.check, JudgeChecker):
            if artifact is None:
                raise ValueError("judge 检查需要 --artifact")
            configured = json.loads(models.read_text(encoding="utf-8")) if models else [
                {"name": "openai", "model": "gpt-4.1-mini"}]
            service = JudgeService([ModelTarget(**entry) for entry in configured])
            output = {"criterion_id": criterion, **service.run(item.check, artifact.read_text(encoding="utf-8"))}
        elif isinstance(item.check, SchemaChecker):
            if artifact is None:
                raise ValueError("schema 检查需要 --artifact")
            value = json.loads(artifact.read_text(encoding="utf-8"))
            output = {"criterion_id": criterion,
                      **check_schema(item.check, value, schema_path(pack, item.check.schema_ref))}
        elif isinstance(item.check, GateChecker):
            output = {"criterion_id": criterion, **check_gate(item.check)}
        else:
            raise ValueError(f"当前 check 尚不支持 {item.check.type} 类型")
        typer.echo(json.dumps(output, ensure_ascii=False))
        if output["status"] != "pass":
            raise typer.Exit(1 if output["status"] in ("fail", "pending") else 3)
    except DslError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except typer.Exit:
        raise
    except (OSError, RuntimeError, SchemaError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        typer.echo(f"检查失败：{exc}", err=True)
        raise typer.Exit(3) from exc
