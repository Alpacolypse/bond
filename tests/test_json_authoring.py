"""Authoring JSON: values cited whole by wrapping them with $bond, parts of strings with tags, stored in one layout."""

import json

import pytest

from bond import Bond, Citation, CitationKind, Format, JsonLocator, Ref, SelfRef, VersionId, VersionLocator, details
from bond.authoring import Tools
from bond.authoring.jsonmarkup import canonical, compile, render
from bond.authoring.markup import Unrenderable
from conftest import json_doc, refused, written


def head(bond: Bond, document: str) -> VersionId:
    """The version a document now points at: what the last write of it stored."""
    return bond.document(document).head


def content(bond: Bond, version: VersionId) -> str:
    text = bond.text(Ref(version, VersionLocator()))
    assert text is not None
    return text


FIRST = "Water leaking through the bathroom ceiling for 8 weeks."
SECOND = "No significant damage to other rooms."
OTHER = VersionId("e" * 64)


def evidence(ref):
    return "E1"


def at(content: str, pointer: str, start: int | None = None, end: int | None = None) -> JsonLocator:
    """The locator of the value a pointer names in content: what Bond stores for it."""
    located = json_doc(content).locate(pointer, None if start is None or end is None else (start, end))
    assert located is not None, pointer
    return located


def cite(tools: Tools, phrase: str) -> str:
    # the transcribed block, not the parse's unsourced copies of its text: every match comes back, with its support
    [hit] = [e for e in tools.search(phrase) if "fax_case_001.reducto" in e.documents and e.support == "supported"]
    return hit.handle


# --- render and compile ---------------------------------------------------------------------


def test_render_wraps_whole_values_and_tags_parts_of_strings():
    content = canonical({"finding": "Leak for 8 weeks. Notice ignored.", "score": 3, "tags": ["a", "b"]})
    quote = Citation(CitationKind.QUOTE, at(content, "/finding", 0, 17), [Ref(OTHER, VersionLocator())])
    citations = [
        quote,
        Citation(CitationKind.PARAPHRASE, at(content, "/finding", 18, 33), [SelfRef(at(content, "/finding", 0, 4))]),
        Citation(CitationKind.ASSESSMENT, at(content, "/score"), [SelfRef(at(content, "/finding", 0, 17))],
                 {"assessor": "reducto"}),
        Citation(CitationKind.UNSOURCED, at(content, "/tags"), []),
        Citation(CitationKind.PARAPHRASE, at(content, "/tags/1"), [SelfRef(at(content, "/score"))]),
    ]
    draft = render(content, citations, evidence)
    assert json.loads(draft) == {
        "finding": "<bond:cite id='c1' kind='quote' from='E1'><bond:anchor id='a1'>Leak</bond:anchor> for 8 "
                   "weeks.</bond:cite> <bond:cite kind='paraphrase' from='#a1'>Notice ignored.</bond:cite>",
        "score": {"$bond": {"cite": {"id": "c2", "kind": "assessment", "from": "#c1", "assessor": "reducto"}}, "value": 3},
        "tags": {"$bond": {"cite": {"kind": "unsourced"}},
                 "value": ["a", {"$bond": {"cite": {"kind": "paraphrase", "from": "#c2"}}, "value": "b"}]},
    }
    compiled = compile(draft)
    assert compiled.issues == [] and compiled.content == content
    assert [(e.kind, e.claim, e.sources, e.id) for e in compiled.entries] == [
        (CitationKind.QUOTE, at(content, "/finding", 0, 17), ["E1"], "c1"),
        (CitationKind.PARAPHRASE, at(content, "/finding", 18, 33), ["#a1"], None),
        (CitationKind.ASSESSMENT, at(content, "/score"), ["#c1"], "c2"),
        (CitationKind.UNSOURCED, at(content, "/tags"), [], None),
        (CitationKind.PARAPHRASE, at(content, "/tags/1"), ["#c2"], None),
    ]
    assert compiled.names == {"c1": at(content, "/finding", 0, 17), "a1": at(content, "/finding", 0, 4),
                              "c2": at(content, "/score")}


def test_several_citations_of_a_value_are_a_list_and_an_anchor_names_a_value():
    # Everything Bond adds to a value is inside "$bond": "cite" and "anchor", mirroring the tags.
    content = canonical({"n": 7, "m": "x"})
    citations = [
        Citation(CitationKind.UNSOURCED, at(content, "/n"), []),
        Citation(CitationKind.QUOTE, at(content, "/n"), [Ref(OTHER, VersionLocator())]),
        Citation(CitationKind.PARAPHRASE, at(content, "/m"), [SelfRef(at(content, ""))]),
    ]
    draft = json.loads(render(content, citations, evidence))
    assert draft["$bond"] == {"anchor": "a1"} and set(draft) == {"$bond", "value"}
    assert draft["value"]["n"]["$bond"] == {"cite": [{"kind": "unsourced"}, {"kind": "quote", "from": "E1"}]}
    compiled = compile(json.dumps(draft))
    assert compiled.issues == [] and compiled.content == content and compiled.names == {"a1": at(content, "")}


def test_content_is_stored_in_one_layout_however_the_draft_is_laid_out():
    compiled = compile('{"b":  "é",\n"a":[1,\n 2]}')
    assert compiled.issues == [] and compiled.content == '{\n  "b": "é",\n  "a": [\n    1,\n    2\n  ]\n}\n'


@pytest.mark.parametrize("draft, message", [
    ('{"a": ', "isn't valid JSON"),
    ('{"a": 1, "a": 2}', "the key 'a' twice"),
    ('{"a": {"$bond": {"cite": {"kind": "quote"}}}}', "it has '$bond'"),
    ('{"a": {"$bond": {"cite": {"kind": "quote"}}, "value": 1, "note": 2}}', "it has '$bond', 'value', 'note'"),
    ('{"a": {"$bond": {"kind": "quote"}, "value": 1}}', 'must be an object with "cite", "anchor" or both'),
    ('{"a": {"$bond": {}, "value": 1}}', 'must be an object with "cite", "anchor" or both'),
    ('{"a": {"$bond": {"cite": {"kind": "vibe"}}, "value": 1}}', "isn't a citation kind"),
    ('{"a": {"$bond": {"cite": {"from": "E1"}}, "value": 1}}', "cite at /a has no kind"),
    ('{"a": {"$bond": {"cite": "quote"}, "value": 1}}', "must be a citation object"),
    ('{"a": {"$bond": {"cite": []}, "value": 1}}', "is an empty list"),
    ('{"a": {"$bond": {"cite": {"kind": "quote", "from": ["E1"]}}, "value": 1}}', '"from" must be a string'),
    ('{"a": {"$bond": {"cite": {"kind": "quote"}}, "value": {"$bond": {"anchor": "x"}, "value": 1}}}', "wrapped twice"),
    ('{"a": {"$bond": {"anchor": 3}, "value": 1}}', "must be a non-empty string"),
    ('{"a": "<bond:cite kind=\'source\'/>x"}', "is self-closing; to cite a whole value, wrap it"),
    ('{"a": "<bond:cite kind=\'quote\'>x"}', "in the string at /a: "),
    ('{"a": {"$bond": {"anchor": "k"}, "value": "<bond:anchor id=\'k\'>x</bond:anchor>"}}', 'have the id "k"'),
])
def test_compile_says_what_is_malformed(draft, message):
    issues = compile(draft).issues
    assert any(message in i for i in issues), issues


def test_render_refuses_what_a_json_draft_cant_show():
    with pytest.raises(Unrenderable, match="every citation claims a value or part of a string"):
        render(canonical({"a": 1}), [Citation(CitationKind.SOURCE, VersionLocator(), [])], evidence)
    with pytest.raises(Unrenderable, match="reserved for citations"):
        render(canonical({"a": {"$bond": 1}}), [], evidence)
    # Only "$bond" is reserved: a key like "$anchor" or "cite" is content.
    assert json.loads(render(canonical({"$anchor": 1, "cite": 2}), [], evidence)) == {"$anchor": 1, "cite": 2}
    with pytest.raises(Unrenderable, match="reserved for citation tags"):
        render(canonical({"a": "see <bond:x>"}), [], evidence)


# --- through the tools ------------------------------------------------------------------


def test_an_agent_writes_a_cited_json_document(bond, case):
    tools = Tools(bond)  # the agent policy: every word cited, quote, paraphrase or unsourced
    draft = json.dumps({
        "complaint": f"<bond:cite id='q' kind='quote' from='{cite(tools, FIRST)}'>{FIRST}</bond:cite>",
        "hip": {"$bond": {"cite": {"kind": "quote", "from": cite(tools, SECOND)}}, "value": SECOND},
        "weeks": {"$bond": {"cite": {"kind": "paraphrase", "from": "#q"}}, "value": 8},
    })
    written(tools.write("intake", draft, format=Format.JSON))
    version = head(bond, "intake")
    assert details.version(bond, version).format is Format.JSON
    assert content(bond, version) == canonical({"complaint": FIRST, "hip": SECOND, "weeks": 8})
    weeks = next(c for c in bond.citations(version) if Ref(version, c.claim) == details.locate_pointer(bond, version, "/weeks"))
    assert weeks.supports == [details.locate_pointer(bond, version, "/complaint", (0, len(FIRST)))]
    # Read back, the draft writes the same version.
    reading = tools.draft("intake")
    again = written(tools.write("intake", reading.text, reading.base))
    assert (head(bond, "intake"), again.advanced) == (version, False)


def test_an_uncited_value_is_named_by_its_path(bond, case):
    tools = Tools(bond)
    note, n = refused(tools.write("intake", json.dumps({"note": "made up", "n": 2}), format=Format.JSON, dry_run=True))
    assert note.startswith("no citation covers 'made up' at /note (wrap the value: {\"$bond\": {\"cite\":")
    assert n.startswith("no citation covers '2' at /n ")
    [source] = refused(tools.write("intake", json.dumps({"c": {"$bond": {"cite": {"kind": "source"}}, "value": 1}}),
                                   format=Format.JSON, dry_run=True))
    assert source.startswith("cite at /c: source citations are not allowed here")  # not an agent's to claim


def test_a_json_document_laid_out_otherwise_is_reformatted_by_its_first_write(bond, case):
    citations = [Citation(CitationKind.UNSOURCED, JsonLocator(()), [])]
    first = bond.write_document("compact", b'{"a":"x","b":[1,2]}', citations, format=Format.JSON).version
    tools = Tools(bond)
    reading = tools.draft("compact")
    out = written(tools.write("compact", reading.text, reading.base))
    version = head(bond, "compact")
    assert out.advanced and version != first
    assert content(bond, version) == canonical({"a": "x", "b": [1, 2]})
    assert [c.id for c in bond.citations(version)] == [c.id for c in bond.citations(first)]


@pytest.mark.parametrize("case_id", ["001", "003"])
def test_every_seeded_json_document_round_trips(tmp_path, case_id):
    import seed

    from bond import ALLOW_GAPS

    with Bond.local(tmp_path / "store") as bond:
        seed.seed(bond, case_id)
        tools = Tools(bond, ALLOW_GAPS)  # the round trip is under test, not the agent policy
        documents = [d for d in bond.documents() if details.version(bond, d.head).format is Format.JSON]
        assert len(documents) >= 1
        for doc in documents:
            reading = tools.draft(doc.name)
            out = written(tools.write(doc.name, reading.text, reading.base))
            assert (head(bond, doc.name), out.advanced) == (doc.head, False), doc.name
