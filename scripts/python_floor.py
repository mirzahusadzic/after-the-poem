"""The study's Python floor, which is Gadfly's (the `midwife` package), so one interpreter runs every script.

fsutil imports this module first, and every program here loads fsutil, so an older Python stops with
a reason before anything is read or written. Keep this file readable by old interpreters: it must
parse there to say why it stops.
"""

import sys

FLOOR = (3, 13)


def check(version=None):
    """Stop with a reason when `version` (default: this interpreter) is below the floor."""
    major, minor = tuple(sys.version_info[:2] if version is None else version[:2])
    if (major, minor) < FLOOR:
        raise SystemExit(
            'After the Poem needs Python %d.%d or later; this is %d.%d. Run it with `uv run python ...` '
            'or through `npm run`, which use the Python and packages uv.lock pins.' % (FLOOR + (major, minor))
        )


check()
