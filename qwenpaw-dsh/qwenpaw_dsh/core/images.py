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
import logging
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
        too-large                 size > :data:`MAX_IMAGE_BYTES`
        unreadable                stat/read raised (missing, directory, perms)

    Never raises for these expected failures.
    """
    mime = mime_for_path(path)
    if mime is None:
        suffix = Path(str(path)).suffix.lower() or "<none>"
        return None, f"unknown-suffix:{suffix}"

    target = Path(str(path))
    try:
        size = target.stat().st_size
    except OSError:
        return None, "unreadable"
    if size > MAX_IMAGE_BYTES:
        return None, "too-large"
    try:
        raw = target.read_bytes()
    except OSError:
        return None, "unreadable"
    return InlineImage(data=base64.b64encode(raw).decode("ascii"), mime=mime), None
