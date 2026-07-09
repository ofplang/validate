"""Entry process resolution (spec 10.3).

Intent: a v0 document must have exactly one entry process. The rule has an
implicit-default form (`main`) so simple documents need no `entry` key, but an
explicit `entry` must name a real process. This pass reports the two failure
modes with distinct codes so authors can tell "you forgot to define an entry"
apart from "your entry points at a missing process".

Process-dependency acyclicity (also spec 10.2/10.3) is validated separately in
the object/graph layer where the full node graph is available.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode


def check_entry(doc: YNode, diags: Diagnostics) -> None:
    if not isinstance(doc, YMap):
        return  # a non-mapping root was already reported by the shape pass

    processes = doc.get("processes")
    process_names = set(processes.keys()) if isinstance(processes, YMap) else set()

    entry_node = doc.get("entry")

    # Implicit form: no `entry` key. `main` is the entry if it exists, otherwise
    # the document has no entry process at all.
    if entry_node is None:
        if "main" not in process_names:
            diags.add(
                errors.NO_ENTRY_PROCESS,
                "no 'entry' and no process named 'main'",
                "entry",
                at=doc,
            )
        return

    # Explicit form: must be a scalar naming an existing process.
    if not isinstance(entry_node, YScalar):
        diags.add(errors.WRONG_VALUE_KIND, "entry must be a string", "entry", at=entry_node)
        return
    if entry_node.text not in process_names:
        diags.add(
            errors.UNKNOWN_ENTRY_PROCESS,
            f"entry names unknown process {entry_node.text!r}",
            "entry",
            at=entry_node,
        )


def _referenced_processes(proc: YMap) -> set[str]:
    """Process names a composite body targets (node process + branch arms).

    This is the edge set of the process dependency graph; only names that turn
    out to be real processes matter for cycle detection.
    """
    refs: set[str] = set()
    body = proc.get("body")
    if not isinstance(body, YMap):
        return refs
    nodes = body.get("nodes")
    if not isinstance(nodes, YSeq):
        return refs
    for item in nodes.items:
        if not isinstance(item, YMap):
            continue
        p = item.get("process")
        if isinstance(p, YScalar):
            refs.add(p.text)
        # branch arms carry their own process targets.
        for arm in ("then", "else"):
            arm_node = item.get(arm)
            if isinstance(arm_node, YMap):
                ap = arm_node.get("process")
                if isinstance(ap, YScalar):
                    refs.add(ap.text)
    return refs


def check_process_dependencies(doc: YNode, diags: Diagnostics) -> None:
    """Reject recursive composite dependencies (spec 10.2).

    The process dependency graph must be acyclic — v0 has no recursion. We run a
    DFS colouring and report a single `recursive_process_dependency` on the first
    back edge (one cycle is enough to condemn the document; enumerating them all
    would just be noise).
    """
    if not isinstance(doc, YMap):
        return
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return

    known = set(processes.keys())
    graph: dict[str, set[str]] = {}
    for pname in known:
        proc = processes.get(pname)
        if isinstance(proc, YMap):
            # Restrict edges to real processes so a dangling name (reported
            # elsewhere) does not masquerade as a cycle.
            graph[pname] = {r for r in _referenced_processes(proc) if r in known}
        else:
            graph[pname] = set()

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {p: WHITE for p in known}

    def dfs(u: str) -> bool:
        color[u] = GRAY
        for v in graph.get(u, ()):  # neighbours
            if color[v] == GRAY:
                return True  # back edge -> cycle
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    for p in known:
        if color[p] == WHITE and dfs(p):
            diags.add(
                errors.RECURSIVE_PROCESS_DEPENDENCY,
                "process dependency graph contains a cycle",
                "processes",
                at=processes,
            )
            return
