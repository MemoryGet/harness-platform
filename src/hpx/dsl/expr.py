"""Small, side-effect-free language for applies_when conditions."""

import ast


class ExpressionError(ValueError):
    pass


ROOTS = {"input", "artifact", "evidence", "env"}
COMPARE = {ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn}


def parse_expression(source: str) -> ast.expr:
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError("条件表达式语法错误") from exc

    def check(node: ast.AST) -> None:
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for child in node.values:
                check(child)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            check(node.operand)
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARE:
            check(node.left)
            check(node.comparators[0])
        elif isinstance(node, ast.Attribute):
            base = node
            while isinstance(base, ast.Attribute):
                if base.attr.startswith("_"):
                    raise ExpressionError("字段名不能以下划线开头")
                base = base.value
            if not isinstance(base, ast.Name) or base.id not in ROOTS:
                raise ExpressionError("字段必须以 input、artifact、evidence 或 env 开头")
        elif isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
            pass
        elif isinstance(node, ast.Name) and node.id in ("true", "false", "null"):
            pass
        elif isinstance(node, (ast.List, ast.Tuple)):
            for child in node.elts:
                if not isinstance(child, ast.Constant):
                    raise ExpressionError("列表只能包含字面量")
                check(child)
        else:
            raise ExpressionError("条件只允许字段比较、字面量、and/or/not 与括号")

    check(tree.body)
    return tree.body


def evaluate_expression(source: str, context: dict) -> bool:
    tree = parse_expression(source)

    def value(node: ast.AST):
        if isinstance(node, ast.BoolOp):
            values = [bool(value(child)) for child in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp):
            return not bool(value(node.operand))
        if isinstance(node, ast.Compare):
            left, right = value(node.left), value(node.comparators[0])
            op = node.ops[0]
            if isinstance(op, ast.Eq): return left == right
            if isinstance(op, ast.NotEq): return left != right
            if isinstance(op, ast.In): return left in right
            if isinstance(op, ast.NotIn): return left not in right
            if left is None or right is None: return False
            if isinstance(op, ast.Lt): return left < right
            if isinstance(op, ast.LtE): return left <= right
            if isinstance(op, ast.Gt): return left > right
            return left >= right
        if isinstance(node, ast.Attribute):
            parts = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            result = context.get(node.id)
            for part in reversed(parts):
                result = result.get(part) if isinstance(result, dict) else None
            return result
        if isinstance(node, ast.Name):
            return {"true": True, "false": False, "null": None}[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [value(child) for child in node.elts]
        return node.value

    try:
        return bool(value(tree))
    except (TypeError, ValueError) as exc:
        raise ExpressionError(f"条件求值失败：{exc}") from exc
