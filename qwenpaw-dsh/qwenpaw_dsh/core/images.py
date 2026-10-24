# -*- coding: utf-8 -*-
"""Turn local image attachments into ACP inline ``image`` blocks.

Why this exists
---------------
The host forwards attachments as :class:`HarnessAttachment` (``kind``/ ``path``
/ ``name``). ACP can carry an image inline as a base64 ``image`` content block
(``acp.helpers.image_block``), but DSH only accepts it when the *route*
advertises ``promptCapabilities.image`` (spike s06). When the probe says no —
or the file cannot be inlined safely — the adapter falls back to the v0.1
text-path forwarding (``Attached file <name>: <path>``).

This module is the pure half: it maps a file suffix to a MIME type and encodes
a readable, size-bounded file to base64. Nothing here raises for the expected
failures; each one is reported as a short reason string so the adapter can log
it and degrade.

Deliberate limits:

* suffix allow-list only (``png``/``jpeg``/``jpg``/``gif``/``webp``/``bmp``);
  an unknown suffix is *not* guessed with :mod:`mimetypes` — a wrong MIME would
  be worse than the text-path fallback.
* ``MAX_IMAGE_BYTES`` (15 MiB) read cap; larger files stay on the text path
  rather than pushing a huge base64 blob through stdio (DSH's own transport
  bound is 50 MiB, but a quarter of that is already beyond any useful prompt).
"""

from __future__ import annotations

import base64
import errno
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("qwenpaw.dsh")

#: File suffix -> IANA media type (lower-case suffixes). ``jpg`` and ``jpeg``
#: both mean JPEG; every other supported family has one spelling.
MIME_BY_SUFFIX: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

#: Hard cap for inlining; a larger file degrades to the text path.
MAX_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class InlineImage:
    """One base64 image ready for ``image_block(data, mime)``."""

    data: str
    mime: str


def mime_for_path(path: Any) -> str | None:
    """Return the MIME type for a supported suffix, else ``None``.

    Suffix matching is case-insensitive and **allow-list based**: an unknown
    suffix returns ``None`` instead of a guessed type.
    """
    suffix = Path(str(path)).suffix.lower()
    return MIME_BY_SUFFIX.get(suffix)


def encode_image(path: Any) -> tuple[InlineImage | None, str | None]:
    """Read ``path`` and return ``(InlineImage, None)`` or ``(None, reason)``.

    Reasons are stable identifiers for logs/tests::

        unknown-suffix:<suffix>   not in :data:`MIME_BY_SUFFIX`
        too-large                 size > :data:`MAX_IMAGE_BYTES` (stat, fstat or read)
        not-regular               symlink/FIFO/device/socket, or inode changed
        unsupported-platform      no O_NONBLOCK; refusing a potentially blocking open
        unreadable                missing, directory, permissions, other I/O error

    Only a real regular file is read. The file is opened with ``O_NONBLOCK``
    (and ``O_NOFOLLOW`` where the platform has it), re-checked with ``fstat``,
    and read in a short-read loop capped at ``MAX_IMAGE_BYTES + 1`` bytes. The
    descriptor is always closed. A symlink is refused rather than followed:
    inlining is a convenience feature, and following a link would let a caller
    swap the target between the size check and the read. A replaced inode is
    rejected on platforms without ``O_NOFOLLOW``; a FIFO replaced after the
    lstat cannot block the open because of ``O_NONBLOCK``. Platforms without
    ``O_NONBLOCK`` are refused conservatively instead of risking a blocking
    open. Never raises for these expected failures.
    """
    mime = mime_for_path(path)
    if mime is None:
        suffix = Path(str(path)).suffix.lower() or "<none>"
        return None, f"unknown-suffix:{suffix}"

    if not hasattr(os, "O_NONBLOCK"):
        return None, "unsupported-platform"

    target = Path(str(path))
    try:
        before = os.lstat(target)
    except FileNotFoundError:
        return None, "unreadable"
    except OSError:
        return None, "unreadable"
    if stat.S_ISLNK(before.st_mode):
        return None, "not-regular"
    if stat.S_ISDIR(before.st_mode):
        # Keep the historical reason for a directory; it is not an image.
        return None, "unreadable"
    if not stat.S_ISREG(before.st_mode):
        return None, "not-regular"
    if before.st_size > MAX_IMAGE_BYTES:
        return None, "too-large"

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= os.O_NONBLOCK
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags |= nofollow
    try:
        fd = os.open(target, flags)
    except OSError as error:
        # ELOOP means the final component became a symlink; either way this is
        # not a file we will inline.
        if error.errno in (errno.ELOOP, errno.ENOENT):
            return None, "not-regular" if error.errno == errno.ELOOP else "unreadable"
        return None, "unreadable"

    try:
        try:
            info = os.fstat(fd)
        except OSError:
            return None, "unreadable"
        if not stat.S_ISREG(info.st_mode):
            return None, "not-regular"
        if info.st_size > MAX_IMAGE_BYTES:
            return None, "too-large"
        if not nofollow and (
            info.st_dev != before.st_dev or info.st_ino != before.st_ino
        ):
            # No O_NOFOLLOW: prove the opened inode is the one we inspected.
            return None, "not-regular"
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_IMAGE_BYTES:
            chunk = os.read(fd, min(64 * 1024, MAX_IMAGE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                return None, "too-large"
            chunks.append(chunk)
        raw = b"".join(chunks)
    except OSError:
        return None, "unreadable"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return InlineImage(data=base64.b64encode(raw).decode("ascii"), mime=mime), None
