import json
from pathlib import Path

import typer

from hpx.checkers.rule import check_rule
from hpx.checkers.judge import JudgeService, ModelTarget
from hpx.dsl.loader import DslError, load_pack
from hpx.knowledge.validate import validate_basis
from hpx.model.pack import JudgeChecker, RuleChecker

app = typer.Typer(help="判据包验证与单条判据调试")


def knowledge_directory(path: Path) -> Path:
    for root in (path.resolve().parent, *path.resolve().parents):
        candidate = root / "knowledge"
        if candidate.is_dir():
            return candidate
    return path.resolve().parent / "knowledge"


@app.command()
def validate(paths: list[Path] = typer.Argument(..., help="一个或多个判据包 YAML")) -> None:
    failed = False
    for path in paths:
        try:
            pack = load_pack(path)
            validate_basis(pack, knowledge_directory(path))
            typer.echo(f"通过 {path} ({pack.metadata.id})")
        except DslError as exc:
            typer.echo(f"失败 {exc}", err=True)
            failed = True
    if failed:
        raise typer.Exit(1)


@app.command()
def check(
    criterion: str = typer.Option(..., help="判据 id"),
    evidence: Path | None = typer.Option(None, help="rule 采集函数输出的 JSON 文件"),
    artifact: Path | None = typer.Option(None, help="judge 待评审的 UTF-8 文本文件"),
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
            if evidence is None:
                raise ValueError("rule 检查需要 --evidence")
            values = json.loads(evidence.read_text(encoding="utf-8"))
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
        else:
            raise ValueError(f"当前 check 尚不支持 {item.check.type} 类型")
        typer.echo(json.dumps(output, ensure_ascii=False))
        if output["status"] != "pass":
            raise typer.Exit(1 if output["status"] == "fail" else 3)
    except DslError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        typer.echo(f"检查失败：{exc}", err=True)
        raise typer.Exit(3) from exc
