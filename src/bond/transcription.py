"""Transcriptions: the text of a source that has none of its own, as a document citing where each part was read.

Some formats hold no text of their own: a PDF is a picture of pages (formats.FormatSpec.has_text is False), and
ingesting one extracts none. Its text is a derived document, one producer's output for one source, stored
byte-for-byte in the producer's output format and filed as "<document>.<producer>". Its citations say what each part
of the output is: text read from a region of a page (TRANSCRIPTION), a label the producer gave a region
(CLASSIFICATION), its judgment of text it read (ASSESSMENT); the rest is UNSOURCED. Writing to it then cites that
text, and a trace follows it down to the region it was read from.

Nothing here knows a format or a producer. A Transcription says which source formats it reads, the format its
output is stored in, and how that output is cited; record() checks the source has no text of its own and is one
the producer reads, and the write checks every region it cites is valid in the source. So a new source format (an
image, a TIFF fax) is a format in the core with its region locator, and a new producer is one Transcription.

Two jobs, kept apart:

- Reading a producer's output into citations is provenance, so it is Bond's: one Transcription per producer
  (bond.importers lists those Bond ships). It is plain code over bytes, with no network.
- Running a producer (credentials, network, cost, sending a document to a vendor) is a deployment's. Bond runs only
  what needs none of that (a Transcriber), such as a PDF's own text layer (bond.importers.text_layer). A deployment
  that runs a vendor records what it returned with record().

A transcription's citations name its producer, and what rests on them can come back supported down to the source.
So its output must reach record() from whatever ran the producer, never by way of a model: an agent may ask for a
transcription (through a tool its deployment offers), but never hands one in.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .api import Bond
from .formats import parse, spec
from .models import BondError, Citation, Format, Version, VersionId, WriteResult
from .parsers import Malformed, ParsedDoc


class TranscriptionError(BondError):
    """A source can't be transcribed this way, or an output isn't the producer's; nothing was written."""


class Transcription(Protocol):
    """How one producer's output is cited: which parts it read from which regions of the source, and what the rest
    is."""

    @property
    def producer(self) -> str:
        """The producer's id, as its citations name it: a lowercase letter, then lowercase letters, digits and -."""
        ...

    @property
    def reads(self) -> frozenset[Format]:
        """The source formats it transcribes: formats with no text of their own."""
        ...

    @property
    def output(self) -> Format:
        """The format its output is stored in, byte-for-byte."""
        ...

    def citations(self, output: ParsedDoc, source: Version) -> list[Citation]:
        """The citations of this producer's output (parsed in its output format, from the bytes to be stored) for
        the source version, whose format it reads; raises ValueError if it isn't this producer's output."""
        ...


@runtime_checkable
class Transcriber(Transcription, Protocol):
    """A producer Bond can run itself, with nothing but the source's bytes."""

    def transcribe(self, source: bytes, format: Format) -> bytes:
        """Its output for a source in one of the formats it reads; raises TranscriptionError if it can't read it."""
        ...


def transcription_name(document: str, producer: str) -> str:
    """The document a producer's transcription of document is filed as."""
    return f"{document}.{producer}"


def record(
    bond: Bond, document: str, transcription: Transcription, output: bytes, *, source: VersionId | None = None,
) -> WriteResult:
    """Write a producer's output for a version of document, as its transcription by that producer.

    source is the version the producer read; pass it whenever the producer ran on something read before now (an
    OCR job sent the bytes earlier, say), since document may hold another version by the time it returns, and
    the output must cite what was read. It must be a version document has held. Without it, the output is taken
    as read from what document holds now.

    The output is stored byte-for-byte in the producer's output format and filed as transcription_name(document,
    producer), advancing it from its head when it exists, so recording again after a new version of the source, or a
    new run, writes its next version (and the same output for the same source changes nothing). Its citations name
    the producer by its id. Raises UnknownDocument; TranscriptionError if the source has text of its own, isn't a format
    the producer reads, or the output isn't the producer's; or WriteError (a region the source doesn't have, say).
    """
    read = source_of(bond, document) if source is None else held_source(bond, document, source)
    source_version = _read_by(read, transcription)
    parsed = parse(transcription.output, output)
    if isinstance(parsed, Malformed):
        raise TranscriptionError(f"not {transcription.producer} output: {parsed.reason}")
    try:
        citations = transcription.citations(parsed, source_version)
    except ValueError as e:
        raise TranscriptionError(str(e)) from None
    name = transcription_name(document, transcription.producer)
    existing = bond.filing.document(name)
    return bond.write_document(name, output, citations, format=transcription.output,
                               base=existing.head if existing else None)


def transcribe(bond: Bond, document: str, transcriber: Transcriber) -> WriteResult:
    """Run transcriber on the source document now holds, and record() what it returns, citing the version it read
    even if document moved on meanwhile."""
    source = _read_by(source_of(bond, document), transcriber)
    output = transcriber.transcribe(bond.blob(source.id), source.format)
    return record(bond, document, transcriber, output, source=source.id)


def held_source(bond: Bond, document: str, version: VersionId) -> Version:
    """version, if document has held it and it can be transcribed. Raises UnknownDocument or TranscriptionError."""
    if all(e.version != version for e in bond.history(document)):
        raise TranscriptionError(f"{document!r} has never held version {version[:12]}…")
    return _transcribable(bond, document, version)


def source_of(bond: Bond, document: str) -> Version:
    """The version document holds, if it can be transcribed: its format has no text of its own. Raises
    UnknownDocument or TranscriptionError."""
    return _transcribable(bond, document, bond.document(document).head)


def _transcribable(bond: Bond, document: str, version: VersionId) -> Version:
    source = bond.records.version(version)
    if source is None:
        raise TranscriptionError(f"{document!r} holds no version here")
    if spec(source.format).has_text:
        raise TranscriptionError(f"{document!r} is {source.format.value}, which has text of its own: it is read, "
                                 "searched and cited as it is, never transcribed")
    return source


def _read_by(source: Version, transcription: Transcription) -> Version:
    """source, if its format is one transcription's producer reads; raises TranscriptionError otherwise."""
    if source.format not in transcription.reads:
        reads = ", ".join(sorted(f.value for f in transcription.reads)) or "nothing"
        raise TranscriptionError(f"{transcription.producer} doesn't read {source.format.value} (it reads {reads})")
    return source
