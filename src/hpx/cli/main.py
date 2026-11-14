import json
from pathlib import Path

import typer

from hpx.checkers.rule import check_rule
from hpx.checkers.judge import JudgeService, ModelTarget
from hpx.checkers.media import media_probe
from hpx.checkers.record import input_get, record_field_check
from hpx.checkers.schema import check_schema
from hpx.dsl.loader import DslError, load_pack
from hpx.knowledge.validate import validate_basis
from hpx.model.pack import JudgeChecker, RuleChecker, SchemaChecker

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
            elif artifact is not None and item.check.fn == "media.probe":
                values = media_probe(artifact)
            elif artifact is not None and item.check.fn in ("input.get", "record.field_check"):
                source = json.loads(artifact.read_text(encoding="utf-8"))
                params = item.check.params
                if item.check.fn == "input.get":
                    values = input_get(source, params["key"])
                else:
                    values = record_field_check(source, params["path"])
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
        else:
            raise ValueError(f"当前 check 尚不支持 {item.check.type} 类型")
        typer.echo(json.dumps(output, ensure_ascii=False))
        if output["status"] != "pass":
            raise typer.Exit(1 if output["status"] == "fail" else 3)
    except DslError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except typer.Exit:
        raise
    except (OSError, RuntimeError, SchemaError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        typer.echo(f"检查失败：{exc}", err=True)
        raise typer.Exit(3) from exc
