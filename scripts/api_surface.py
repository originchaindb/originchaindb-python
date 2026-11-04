"""Parse SDK-owned signatures, exports and declared model fields with Python AST."""

import ast
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
surface = {}
for path in sorted((root / "originchain").glob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = "originchain." + path.stem
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            surface[module + "." + node.name] = (
                ("async " if isinstance(node, ast.AsyncFunctionDef) else "")
                + ast.unparse(node.args)
                + " -> "
                + (ast.unparse(node.returns) if node.returns else "Any")
            )
        if isinstance(node, ast.ClassDef):
            # Private resource classes implement public client.rows/sql/etc.
            visible = not node.name.startswith("_") or any(
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
                for item in node.body
            )
            if not visible:
                continue
            prefix = module + "." + node.name
            surface[prefix] = "bases: " + ", ".join(ast.unparse(base) for base in node.bases)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    not item.name.startswith("_") or item.name == "__init__"
                ):
                    decorators = ",".join(
                        ast.unparse(d)
                        for d in item.decorator_list
                        if isinstance(d, ast.Name)
                        and d.id in ("classmethod", "staticmethod", "property")
                    )
                    surface[prefix + "." + item.name] = (
                        decorators
                        + (" async " if isinstance(item, ast.AsyncFunctionDef) else " ")
                        + ast.unparse(item.args)
                        + " -> "
                        + (ast.unparse(item.returns) if item.returns else "Any")
                    )
                elif (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and not item.target.id.startswith("_")
                ):
                    surface[prefix + "." + item.target.id] = ast.unparse(item.annotation) + (
                        " required" if item.value is None else " default=" + ast.unparse(item.value)
                    )
        if path.name == "__init__.py" and isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            ):
                for name in ast.literal_eval(node.value):
                    surface["export." + name] = "public"
print(json.dumps(surface, sort_keys=True))
