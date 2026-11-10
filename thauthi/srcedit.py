"""Comment-preserving source rewrite shared by every `--apply` code path."""

import ast


def splice_source(old_src: str, new_tree: ast.Module) -> str:
    """
    Return `old_src` with only the top-level statements that differ in `new_tree` replaced
    by their unparsed form, so comments and formatting everywhere else survive.
    Falls back to a full `ast.unparse` when statements cannot be matched 1:1 by line span.
    """
    old = ast.parse(old_src)
    share_a_line = any(a.end_lineno >= b.lineno for a, b in zip(old.body, old.body[1:]))
    if len(old.body) != len(new_tree.body) or share_a_line:
        return ast.unparse(new_tree) + "\n"

    lines = old_src.splitlines(keepends=True)
    for old_node, new_node in reversed(list(zip(old.body, new_tree.body))):
        if ast.dump(old_node) != ast.dump(new_node):
            first = min([old_node.lineno] + [d.lineno for d in getattr(old_node, "decorator_list", [])])
            # shortcut: comments inside a rewritten statement are still lost (ast has no
            # comments); switch to a CST library if that ever matters.
            lines[first - 1 : old_node.end_lineno] = [ast.unparse(new_node) + "\n"]
    return "".join(lines)
