"""Script process restrictions (spec 22, 22.1).

Intent: a script process is an atomic process with inline Pure Data computation.
v0 restricts it hard: the only portable language is `python`, all ports must be
Pure Data, and it must not declare an `objects` section (scripts never touch
Object identity). This pass owns validation of a script process's interface;
the Object-tracking pass deliberately skips script processes so their Object
ports are reported here with the precise `script_object_port` code rather than
as a generic incompleteness.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import (
    TypeEnv,
    TypeParseError,
    is_object_bearing,
    parse_type,
    process_type_params,
)
from ofplang.validate.yamlnode import YMap, YScalar, YNode


def _has_object_port(ports: YNode | None, env: TypeEnv, tp: dict[str, str]) -> bool:
    if not isinstance(ports, YMap):
        return False
    for pname in ports.keys():
        port = ports.get(pname)
        if isinstance(port, YMap):
            tnode = port.get("type")
            if isinstance(tnode, YScalar) and tnode.is_str:
                try:
                    if is_object_bearing(parse_type(tnode.text), env, tp):
                        return True
                except TypeParseError:
                    pass
    return False


def check_scripts(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        script = proc.get("script")
        if not isinstance(script, YMap):
            continue  # not a script process
        base = f"processes.{pname}"
        tp = process_type_params(proc)

        # Only `python` is portable v0 (spec 22).
        lang = script.get("language")
        if isinstance(lang, YScalar) and lang.text != "python":
            diags.add(errors.UNSUPPORTED_SCRIPT_LANGUAGE, f"unsupported language {lang.text!r}", f"{base}.script.language", at=lang)

        # All ports must be Pure Data (spec 22.1).
        if _has_object_port(proc.get("inputs"), env, tp) or _has_object_port(proc.get("outputs"), env, tp):
            diags.add(errors.SCRIPT_OBJECT_PORT, "script process ports must be Pure Data", base, at=proc)

        # A script process must not declare Object behavior (spec 22.1).
        if proc.get("objects") is not None:
            diags.add(errors.SCRIPT_HAS_OBJECTS, "script process must not have an objects section", f"{base}.objects", at=proc.get("objects"))
