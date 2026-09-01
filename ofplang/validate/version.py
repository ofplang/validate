"""The specification revision this implementation implements (spec 2.1).

Kept in a module of its own because everything that needs it would otherwise
have to import a pass: the shape pass checks a document's declaration against
it, and the CLI reports it. A document declaring a later MINOR of this MAJOR,
or any other MAJOR, is refused; an earlier MINOR is accepted and read by these
rules.
"""

from __future__ import annotations

#: The revision of SPECIFICATION.md this implementation implements.
SPEC_VERSION = "0.1"
