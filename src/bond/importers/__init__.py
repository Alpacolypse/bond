"""How each producer's output is cited as a transcription (bond.transcription): one module per producer.

- text_layer: a PDF's own text layer, read with pypdf. Bond runs it: it needs nothing but the PDF.
- reducto: Reducto's parse. Bond reads it; whoever runs Reducto records it.

A new producer is a module here with its Transcription (or Transcriber, if Bond can run it with nothing but the
source's bytes), listed in TRANSCRIPTIONS by its producer id.
"""

from __future__ import annotations

from ..models import Format
from ..transcription import Transcriber, Transcription
from .reducto import REDUCTO
from .text_layer import TEXT_LAYER

TRANSCRIPTIONS: dict[str, Transcription] = {t.producer: t for t in (TEXT_LAYER, REDUCTO)}


def transcribers_for(fmt: Format) -> list[Transcriber]:
    """The producers Bond can run itself on a source in fmt, in the order TRANSCRIPTIONS lists them."""
    return [t for t in TRANSCRIPTIONS.values() if isinstance(t, Transcriber) and fmt in t.reads]


__all__ = ["TRANSCRIPTIONS", "transcribers_for"]
