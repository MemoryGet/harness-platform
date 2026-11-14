import json
from pathlib import Path

import typer

from hpx.checkers.rule import check_rule
from hpx.dsl.loader import DslError, load_pack
from hpx.model.pack import RuleChecker

app = typer.Typer(help="判据包验证与单条判据调试")


@app.command()
def validate(paths: list[Path] = typer.Argument(..., help="一个或多个判据包 YAML")) -> None:
    failed = False
    for path in paths:
        try:
            pack = load_pack(path)
            typer.echo(f"通过 {path} ({pack.metadata.id})")
        except DslError as exc:
            typer.echo(f"失败 {exc}", err=True)
            failed = True
    if failed:
        raise typer.Exit(1)


@app.command()
def check(
    criterion: str = typer.Option(..., help="判据 id"),
    evidence: Path = typer.Option(..., help="采集函数输出的 JSON 文件"),
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
        if not isinstance(item.check, RuleChecker):
            typer.echo(f"判据 {criterion} 不是 rule 类型，当前 check 只支持 rule", err=True)
            raise typer.Exit(2)
        values = json.loads(evidence.read_text(encoding="utf-8"))
        results = check_rule(item.check, values)
        passed = all(row["ok"] for row in results)
        typer.echo(json.dumps({"criterion_id": criterion, "status": "pass" if passed else "fail",
                               "assertions": results}, ensure_ascii=False))
        if not passed:
            raise typer.Exit(1)
    except DslError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        typer.echo(f"检查失败：{exc}", err=True)
        raise typer.Exit(3) from exc
