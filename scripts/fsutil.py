#!/usr/bin/env python3
"""Atomic file persistence helpers.

Every writer here stages bytes in a temporary file inside the destination
directory, flushes it to disk, and then publishes it with os.replace so a
crash or a failed write never leaves a half-written file behind. Readers
either see the previous valid file or the new complete file.
"""

import json
import os
import stat
import tempfile
from pathlib import Path

# Read once, while importing is still single-threaded: os.umask can only be read by setting it.
_UMASK = os.umask(0)
os.umask(_UMASK)


def write_bytes_atomic(path, data, *, mode=None):
    """Write bytes atomically: temp file in the same directory + os.replace.

    `mode` is an optional chmod (for example 0o400) applied to the staged
    file before it replaces the destination. Without it, a rewrite keeps the
    destination's mode and a new file gets the mode a plain write would give,
    never mkstemp's owner-only 0o600.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError('Atomic byte writes require bytes')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o666 & ~_UMASK
    fd, temporary = tempfile.mkstemp(
        prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(bytes(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return path
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)
    return path


def write_text_atomic(path, text, *, encoding='utf-8', mode=None):
    """Write text atomically with the same temp-file discipline."""
    if not isinstance(text, str):
        raise TypeError('Atomic text writes require str')
    return write_bytes_atomic(path, text.encode(encoding), mode=mode)


def write_json_atomic(path, value, *, mode=None):
    """Write a JSON document atomically (UTF-8, indent 2, trailing newline)."""
    return write_bytes_atomic(
        path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'), mode=mode
    )
