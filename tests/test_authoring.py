"""Authoring: cite by handles in inline tags, edit from an explicit base, and get issues in the writer's terms."""

import dataclasses
import re

import pytest
import seed

from bond import (
    ALLOW_GAPS,
    Bond,
    CitationKind,
    Format,
    Ref,
    SelfRef,
    SupportStatus,
    TextLocator,
    VersionId,
    VersionLocator,
    details,
)
from bond.authoring import AGENT_POLICY, Checked, Edit, Shortfall, ToolError, Tools
from bond.authoring.markup import Unrenderable, parse, render
from bond.authoring.tools import TRACE_TEXT
from bond.models import Citation, SupportReason
from conftest import refused, written

FIRST = "Water leaking through the bathroom ceiling for 8 weeks."
SECOND = "No significant damage to other rooms."


def cite(tools: Tools, phrase: str) -> str:
    """The handle of the one match for phrase in the fax's OCR output (written documents are evidence too)."""
    # the transcribed block, not the parse's unsourced copies of its text: every match comes back, with its support
    [hit] = [e for e in tools.search(phrase) if "fax_case_001.reducto" in e.documents and e.support == "supported"]
    return hit.handle


def head(bond: Bond, document: str) -> VersionId:
    """The version a document now points at: what the last write of it stored."""
    return bond.document(document).head


def content(bond: Bond, version: VersionId) -> str:
    text = bond.text(Ref(version, VersionLocator()))
    assert text is not None
    return text


def summary(tools: Tools) -> str:
    return (
        f'<bond:cite kind="unsourced">Findings</bond:cite>\n\n'
        f'- <bond:cite kind="quote" from="{cite(tools, FIRST)}">{FIRST}</bond:cite>\n'
        f'- <bond:cite kind="quote" from="{cite(tools, SECOND)}">{SECOND}</bond:cite>\n'
    )


# --- binding ------------------------------------------------------------------------------


def test_tools_only_binds_a_workspace_policy_and_sources(bond, case):
    tools = Tools(bond)
    assert tools.policy is AGENT_POLICY
    draft = summary(tools)
    written(tools.write("a", draft)), written(tools.write("b", draft))
    assert head(bond, "a") == head(bond, "b")  # same bytes and citations
    assert tools.draft("a") == dataclasses.replace(tools.draft("b"), document="a")


# --- writing --------------------------------------------------------------------------


def test_an_agent_writes_a_cited_document(bond, case):
    tools = Tools(bond)
    out = written(tools.write("findings", summary(tools)))
    version = head(bond, "findings")
    assert out.advanced
    assert out.base == version[:8]  # the base for the next change
    # The heading is unsourced, so not all of it rests on declared sources.
    assert out.support is SupportStatus.PARTIALLY_SUPPORTED
    assert content(bond, version) == f"Findings\n\n- {FIRST}\n- {SECOND}\n"  # markup is never stored
    assert [c.kind for c in bond.citations(version)] == [
        CitationKind.UNSOURCED, CitationKind.QUOTE, CitationKind.QUOTE]
    assert bond.verify(version) == []


def test_issues_are_in_the_writers_terms_and_a_dry_run_stores_nothing(bond, case):
    tools = Tools(bond)
    draft = summary(tools) + "\nLandlord ignored every notice.\n"
    before = len(details.versions(bond))
    [issue] = refused(tools.write("findings", draft, dry_run=True))
    assert "Landlord ignored every notice." in issue and not re.search(r"\d+, \d+|start|end", issue)
    assert len(details.versions(bond)) == before
    # A dry run that finds nothing to stop the write says so, and still stores nothing.
    assert tools.write("findings", summary(tools), dry_run=True) == Checked()
    assert len(details.versions(bond)) == before and bond.filing.document("findings") is None


def test_a_token_that_isnt_a_handle_is_refused(bond, case):
    tools = Tools(bond)
    [issue] = refused(tools.write("x", f'<bond:cite kind="quote" from="E99">{FIRST}</bond:cite>'))
    assert "E99" in issue and "search()" in issue


def test_a_handle_names_its_span_outright(bond, case):
    tools = Tools(bond)
    handle = cite(tools, FIRST)
    # The version's id prefix, then the locator written out: here a range inside one value of the OCR output.
    assert re.fullmatch(case["fax"][:8] + r"/result/chunks/\d+/blocks/\d+/content@\d+-\d+", handle)
    # Nothing is remembered, so a handle written out by hand works as well as one a search returned.
    written(Tools(bond).write("x", f'<bond:cite kind="quote" from="{handle}">{FIRST}</bond:cite>'))
    # A handle naming no version, or a range outside its text, names nothing.
    [issue] = refused(tools.write("y", '<bond:cite kind="paraphrase" from="00000000@0-5">Leg pain.</bond:cite>'))
    assert "no version's id starts with '00000000'" in issue
    beyond = re.sub(r"@\d+-\d+$", "@0-99999", handle)
    [issue] = refused(tools.write("z", f'<bond:cite kind="paraphrase" from="{beyond}">Leg pain.</bond:cite>'))
    assert "doesn't fit its version" in issue


def test_a_loop_names_the_tags(bond, case):
    tools = Tools(bond)
    draft = (f'<bond:cite id="a" kind="paraphrase" from="#b">{FIRST}</bond:cite>\n\n'
             f'<bond:cite id="b" kind="paraphrase" from="#a">{SECOND}</bond:cite>\n')
    [issue] = refused(tools.write("x", draft))
    assert issue.startswith('<bond:cite id="a"> rests on <bond:cite id="b"> rests on <bond:cite id="a">')
    assert not re.search(r"\d", issue)


def test_the_host_policy_keeps_an_agent_from_marking_its_text_source(bond, case):
    tools = Tools(bond)
    [issue] = refused(tools.write("x", '<bond:cite kind="source">My own judgment.</bond:cite>'))
    assert "source citations are not allowed" in issue and '<bond:cite kind="source">' in issue


def test_a_misquote_names_the_tag(bond, case):
    tools = Tools(bond)
    handle = cite(tools, FIRST)
    [issue] = refused(tools.write("x", f'<bond:cite id="q" kind="quote" from="{handle}">Water leaking for months.</bond:cite>'))
    assert issue.startswith('<bond:cite id="q">') and "does not match" in issue


def test_a_conclusion_can_rest_on_the_documents_own_tags(bond, case):
    tools = Tools(bond)
    draft = (f'<bond:cite id="pain" kind="quote" from="{cite(tools, FIRST)}">{FIRST}</bond:cite>\n\n'
             '<bond:cite kind="paraphrase" from="#pain">Leg pain for two months.</bond:cite>\n')
    written(tools.write("x", draft))
    version = head(bond, "x")
    conclusion = bond.citations(version)[1]
    assert conclusion.supports == [Ref(version, TextLocator(0, len(FIRST)))]
    assert conclusion.as_written().supports == [SelfRef(TextLocator(0, len(FIRST)))]


def test_an_anchor_lets_a_conclusion_rest_on_part_of_a_sentence(bond, case):
    tools = Tools(bond)
    phrase = "Water leaking"
    draft = (f'<bond:cite kind="quote" from="{cite(tools, FIRST)}"><bond:anchor id="pain">{phrase}</bond:anchor>'
             f'{FIRST[len(phrase):]}</bond:cite>\n\n'
             f'<bond:cite kind="paraphrase" from="#pain">{phrase} is the main complaint.</bond:cite>\n')
    written(tools.write("x", draft))
    version = head(bond, "x")
    quote, conclusion = bond.citations(version)  # the anchor adds no citation
    assert conclusion.as_written().supports == [SelfRef(TextLocator(0, len(phrase)))]
    # The anchor marks its place: the same words elsewhere (here, in the conclusion itself) don't confuse it.
    assert content(bond, version).count(phrase) == 2
    # Draft it back shows the anchor where it was.
    assert f'<bond:anchor id="a1">{phrase}</bond:anchor>' in tools.draft("x").text


def test_removing_an_anchor_that_a_tag_rests_on_is_refused(bond, case):
    tools = Tools(bond)
    phrase = "Water leaking"
    draft = (f'<bond:cite kind="quote" from="{cite(tools, FIRST)}"><bond:anchor id="pain">{phrase}</bond:anchor>'
             f'{FIRST[len(phrase):]}</bond:cite>\n\n'
             f'<bond:cite kind="paraphrase" from="#pain">Back pain dominates.</bond:cite>\n')
    written(tools.write("x", draft))
    base = tools.draft("x").base
    [issue] = refused(tools.edit("x", [Edit(f'<bond:anchor id="a1">{phrase}</bond:anchor>', phrase)], base))
    assert "#a1" in issue and "bond:anchor" in issue


def test_html_in_markdown_is_content_and_the_bond_prefix_is_reserved(bond, case):
    tools = Tools(bond)
    handle = cite(tools, FIRST)
    written(tools.write("x", f'<bond:cite kind="paraphrase" from="{handle}">Leg pain, <cite>8 weeks</cite>.</bond:cite>'))
    assert content(bond, head(bond, "x")) == "Leg pain, <cite>8 weeks</cite>."
    issues = refused(tools.write("y", f'<bond:note>hi</bond:note> <bond:cite kind="quote" from="{handle}">{FIRST}</bond:cite>'))
    assert len(issues) == 2 and all("reserved" in i for i in issues)  # <bond:note> and </bond:note>


# --- the base -------------------------------------------------------------------------


def test_changing_a_document_that_exists_needs_its_base(bond, case):
    tools = Tools(bond)
    first = written(tools.write("findings", summary(tools)))
    [issue] = refused(tools.write("findings", summary(tools)))
    assert "pass the base draft() returned" in issue
    # The base names a whole version; a span, or a prefix that names nothing, isn't one.
    [issue] = refused(tools.write("findings", summary(tools), base=cite(tools, FIRST)))
    assert "names part of a version" in issue
    [issue] = refused(tools.write("findings", summary(tools), base="00000000"))
    assert "no version's id starts with" in issue
    again = written(tools.write("findings", summary(tools), base=first.base))  # the same text again: nothing changes
    assert not again.advanced and again.base == first.base


def test_a_change_from_a_stale_base_is_refused(bond, case):
    tools = Tools(bond)
    tools.write("findings", summary(tools))
    base = tools.draft("findings").base  # two writers both start here
    written(tools.edit("findings", [Edit("Findings", "Results")], base))
    [issue] = refused(tools.edit("findings", [Edit("Findings", "Summary")], base))
    assert "changed since the base you started from" in issue


def test_each_write_returns_the_base_for_the_next(bond, case):
    tools = Tools(bond)
    out = written(tools.write("findings", summary(tools)))
    out = written(tools.edit("findings", [Edit("Findings", "Results")], out.base))
    out = written(tools.edit("findings", [Edit("Results", "Summary")], out.base))  # no read in between
    assert content(bond, head(bond, "findings")).startswith("Summary\n")


# --- editing --------------------------------------------------------------------------


def test_an_edit_carries_untouched_citations_forward(bond, case):
    tools = Tools(bond)
    written(tools.write("findings", summary(tools)))
    first = head(bond, "findings")
    reading = tools.draft("findings")
    assert reading.text.count("<bond:cite") == 3
    assert [e.documents for e in reading.evidence] == [["fax_case_001.reducto"]] * 2  # OCR text, ligatures and all
    new = "Active drip from the bathroom ceiling light fixture."
    old = re.findall(r"<bond:cite [^>]*>.*?</bond:cite>", reading.text)[2]
    written(tools.edit("findings", [Edit(old, f'<bond:cite kind="quote" from="{cite(tools, new)}">{new}</bond:cite>')],
                       reading.base))
    edited = head(bond, "findings")
    assert edited != first
    before, after = bond.citations(first), bond.citations(edited)
    assert [(c.kind, c.claim, c.supports) for c in after[:2]] == [(c.kind, c.claim, c.supports) for c in before[:2]]
    assert bond.text(Ref(edited, after[2].claim)) == new
    assert bond.history("findings")[-1].base == first


def test_an_edit_matching_more_than_once_is_refused_with_every_match(bond, case):
    tools = Tools(bond)
    base = written(tools.write("findings", summary(tools))).base
    [issue] = refused(tools.edit("findings", [Edit("</bond:cite>", "")], base))
    assert "occurs 3 times" in issue and "line 1" in issue and "line 3" in issue
    assert len(bond.history("findings")) == 1  # nothing was written


def test_edits_apply_together_or_not_at_all(bond, case):
    tools = Tools(bond)
    base = written(tools.write("findings", summary(tools))).base
    [issue] = refused(tools.edit("findings", [Edit("Findings", "Results"), Edit("not in the text", "x")], base))
    assert issue.startswith("edit 2: 'not in the text' isn't in the text")
    assert len(bond.history("findings")) == 1
    # An edit that leaves words uncovered is refused as a whole too.
    [issue] = refused(tools.edit("findings", [Edit(SECOND + "</bond:cite>\n",
                                                   SECOND + "</bond:cite>\n- Pending review.\n")], base))
    assert issue.startswith("no citation covers 'Pending review.'")
    assert len(bond.history("findings")) == 1


# --- viewing and searching ----------------------------------------------------------------


def test_read_shows_a_document_exactly_as_it_is(bond, case):
    tools = Tools(bond)
    policy = tools.read("policy_ip112")
    assert policy.format is Format.MARKDOWN and policy.text == content(bond, case["policy"])
    assert "## Repair and Deduct Cases" in policy.text and "<bond:" not in policy.text  # headings shown, no tags or handles
    fax = tools.read("fax_case_001.reducto")
    assert fax.format is Format.JSON and fax.text == content(bond, case["fax"])  # the vendor's JSON, every field
    with pytest.raises(ToolError, match="documents that transcribe it: 'fax_case_001.reducto'"):
        tools.read("fax_case_001")
    with pytest.raises(ToolError, match="no document"):
        tools.read("missing")


def test_search_one_document_for_several_phrases(bond, case):
    tools = Tools(bond)
    hits = tools.search(["written notice", "Active drip"], document="policy_ip112")
    assert hits and all(h.documents == ["policy_ip112"] for h in hits)
    assert {h.phrase for h in hits} == {"written notice"}  # the inspection finding isn't in the policy
    both = tools.search(["written notice", "Active drip"])
    assert {h.phrase for h in both} == {"written notice", "Active drip"}


def test_search_within_a_passage_finds_the_phrase_there(bond, case):
    tools = Tools(bond)
    passage = cite(tools, FIRST)
    elsewhere = [e for e in tools.search("bathroom") if e.text.lower() == "bathroom"]
    assert len(elsewhere) > 1  # "bathroom" is all over the fax
    [inside] = tools.search("bathroom", within=passage)  # the one in this sentence
    assert inside.text == "bathroom" and inside.handle != passage
    assert inside.handle in {e.handle for e in elsewhere}
    with pytest.raises(ToolError, match="isn't a handle"):
        tools.search("bathroom", within="not a handle")
    with pytest.raises(ToolError, match="not both"):  # the span is in one version already
        tools.search("bathroom", document="fax_case_001.reducto", within=passage)


def test_a_hit_says_where_it_is_and_what_passage_it_sits_in(bond, case):
    tools = Tools(bond)
    hits = tools.search("Active drip", document="fax_case_001.reducto")
    # Every match comes back, with its support: the transcribed block, and the parse's unsourced copies of it.
    [hit] = [h for h in hits if h.support is SupportStatus.SUPPORTED]
    assert {h.support for h in hits} == {SupportStatus.SUPPORTED, SupportStatus.UNSUPPORTED}
    assert hit.text == "Active drip"  # the handle cites exactly the match
    assert hit.kinds == [CitationKind.TRANSCRIPTION]
    assert hit.where.startswith("page 2 · /result/chunks/")  # the page it was read from
    assert "▸Active drip◂ from the bathroom" in hit.context
    passage = hit.passage
    assert passage is not None
    exam = tools.trace(passage).spans[0].text
    assert exam is not None and exam.startswith("Inspection: Active drip")
    # The passage can be cited as a whole, and searching within it gives the match back.
    assert tools.search("Active drip", within=passage)[0].handle == hit.handle
    written(tools.write("x", f'<bond:cite kind="paraphrase" from="{passage}">The exam found a positive '
                             'active drip.</bond:cite>'))


def test_a_markdown_hit_gives_its_heading_path_and_line(bond, case):
    tools = Tools(bond)
    hits = tools.search("written notice", document="policy_ip112")
    assert [h.where for h in hits] == [
        "Northwind Legal Aid: Intake Policy IP-112 › Repair and Deduct Cases › Criteria · line 10",
        "Northwind Legal Aid: Intake Policy IP-112 › Repair and Deduct Cases › Definitions · line 15",
    ]


def test_repeated_text_comes_back_as_separate_hits_to_choose_from(bond, case):
    tools = Tools(bond)
    hits = tools.search("high", document="fax_case_001.reducto")  # every block's confidence is "high"
    assert len(hits) > 1 and len({h.handle for h in hits}) == len(hits)
    assert len({h.where for h in hits}) == len(hits)  # each says which block it is


# --- reading --------------------------------------------------------------------------


def test_reading_errors_are_tool_errors(bond, case):
    tools = Tools(bond)
    with pytest.raises(ToolError, match="no document"):
        tools.draft("missing")
    with pytest.raises(ToolError, match=r"is pdf, which can't be read for editing yet \(only markdown, json, csv, tsv\)"):
        tools.draft("fax_case_001")


def test_only_formats_with_a_codec_can_be_written_and_a_document_keeps_its_format(bond, case):
    tools = Tools(bond)
    [issue] = refused(tools.write("scan", "%PDF-1.4", format=Format.PDF))
    assert issue == "pdf documents can't be written yet (only markdown, json, csv, tsv)"
    out = written(tools.write("notes", summary(tools)))
    [issue] = refused(tools.write("notes", summary(tools), out.base, format=Format.JSON))
    assert issue == "'notes' is markdown; a document keeps its format"


@pytest.mark.parametrize("case_id", ["001", "003"])
def test_every_seeded_markdown_document_round_trips(tmp_path, case_id):
    with Bond.local(tmp_path / "store") as bond:
        seed.seed(bond, case_id)
        tools = Tools(bond, ALLOW_GAPS)  # the round trip is under test, not the agent policy
        markdown = [d for d in bond.documents() if details.version(bond, d.head).format is Format.MARKDOWN]
        assert len(markdown) >= 3
        for doc in markdown:
            reading = tools.draft(doc.name)
            out = written(tools.write(doc.name, reading.text, reading.base))
            assert (head(bond, doc.name), out.advanced) == (doc.head, False), doc.name


# --- markup and handles ---------------------------------------------------------------------


def test_parse_nested_and_whole_version_tags():
    parsed = parse('<bond:cite kind="source"/>A <bond:cite id="c1" kind="paraphrase" from="E1">b '
                   '<bond:cite kind="quote" from="E2">c</bond:cite></bond:cite> d')
    assert parsed.issues == [] and parsed.content == "A b c d"
    assert [(t.kind.value, t.span, t.sources) for t in parsed.tags] == [
        ("source", None, []), ("paraphrase", (2, 5), ["E1"]), ("quote", (4, 5), ["E2"])]


def test_parse_takes_either_quote_style():
    parsed = parse("""<bond:cite kind='quote' from='E1 E2' note="it's">x</bond:cite>""")
    assert parsed.issues == [] and parsed.content == "x"
    [tag] = parsed.tags
    assert (tag.kind.value, tag.sources, tag.attrs) == ("quote", ["E1", "E2"], {"note": "it's"})


def test_parse_reports_malformed_markup():
    issues = parse('<bond:cite from="E1">x</bond:cite> </bond:cite> <bond:cite kind="quote">y '
                   '<bond:cite id="a" kind="quote">z</bond:cite><bond:cite id="a" kind="vibe">w</bond:cite>').issues
    assert any("has no kind" in i for i in issues)
    assert any("no opening tag" in i for i in issues)
    assert any("never closed" in i for i in issues)
    assert any('two tags or anchors have id="a"' in i for i in issues)
    assert any("isn't a citation kind" in i for i in issues)
    # Anchors name text, so they need an id and nothing else, and tags and anchors must nest.
    issues = parse('<bond:anchor>x</bond:anchor> <bond:anchor id="k" kind="quote">y</bond:anchor> <bond:anchor id="e"/> '
                   '<bond:cite id="c" kind="quote"><bond:anchor id="p">z</bond:cite></bond:anchor>').issues
    assert any("has no id" in i for i in issues)
    assert any("takes no kind or from" in i for i in issues)
    assert any("wraps no text" in i for i in issues)
    assert any("must nest, never partly overlap" in i for i in issues)


def test_every_locator_type_round_trips_through_a_handle():
    from bond import JsonLocator, PdfLocator, VersionLocator
    from bond.authoring.handles import BadHandle, Pointer, format_handle, parse_handle

    # A handle names a JSON value by its pointer; the tools position it in its version (details.locate_pointer).
    locators = [
        VersionLocator(), TextLocator(305, 393), Pointer("/result/chunks/1/blocks/4/content"),
        Pointer("/result/chunks/1/blocks/4/content", (6, 33)), Pointer(""), Pointer('/a b/c@d/"q"'),
        Pointer("/x~1y/~0z", (1, 2)), PdfLocator(1, (0.114, 0.294, 0.581, 0.352)),
        PdfLocator(0, (0.1, 1 / 3, 0.9, 0.95)),
    ]
    for loc in locators:
        handle = format_handle("c3a863cb", loc)
        assert " " not in handle and '"' not in handle
        assert parse_handle(handle) == ("c3a863cb", loc), handle
    assert format_handle("c3a863cb", Pointer("/result/chunks/1/blocks/4/content", (6, 33))) == \
        "c3a863cb/result/chunks/1/blocks/4/content@6-33"
    assert format_handle("c3a863cb", Pointer("/it's")) == "c3a863cb/it%27s"  # never a quote
    with pytest.raises(TypeError, match="by pointer"):  # a stored JSON locator holds no keys to write out
        format_handle("c3a863cb", JsonLocator((0, 2, 1)))
    for bad in ["E3fa9c1", "c3a863cb@12", "c3a863cb:p0:1,2,3,4", "#c1", "c3a863cb/a@1-x", "c3a863cb:c5d41e09bba2",
                "@c1", "c3a863cb@5-3", "c3a863cb:p1:0.5,0,0.4,1", "c3a863cb/a~2"]:
        problem = parse_handle(bad)
        assert isinstance(problem, BadHandle) and problem.message.startswith(f"{bad!r} isn't a handle"), bad


def test_render_refuses_what_tags_cant_show():
    def evidence(ref):
        return "E1"

    overlapping = [Citation(CitationKind.UNSOURCED, TextLocator(0, 3), []),
                   Citation(CitationKind.UNSOURCED, TextLocator(2, 5), [])]
    with pytest.raises(Unrenderable, match="partly overlap"):
        render("abcdef", overlapping, evidence)
    with pytest.raises(Unrenderable, match="reserved"):
        render("see <bond:x>", [], evidence)


def test_render_places_an_anchor_where_a_support_rests_on_part_of_a_claim():
    content = "Leak for 8 weeks. Notice ignored."
    citations = [
        Citation(CitationKind.UNSOURCED, TextLocator(0, 17), []),
        Citation(CitationKind.PARAPHRASE, TextLocator(18, 33), [SelfRef(TextLocator(0, 4))]),
    ]
    text = render(content, citations, lambda ref: "E1")
    assert text == ('<bond:cite kind="unsourced"><bond:anchor id="a1">Leak</bond:anchor> for 8 '
                    'weeks.</bond:cite> <bond:cite kind="paraphrase" from="#a1">Notice ignored.</bond:cite>')
    parsed = parse(text)
    assert parsed.content == content and parsed.anchors == {"a1": (0, 4)} and parsed.issues == []


def test_render_names_only_tags_something_refers_to():
    content = "Leak for 8 weeks. Notice ignored. Sure."
    quote = Citation(CitationKind.QUOTE, TextLocator(0, 17), [Ref(VersionId("v" * 64), TextLocator(0, 4))])
    citations = [
        Citation(CitationKind.ASSESSMENT, TextLocator(34, 39), [SelfRef(TextLocator(0, 17))], {"assessor": "reducto"}),
        Citation(CitationKind.PARAPHRASE, TextLocator(18, 33), [SelfRef(TextLocator(0, 17))]),
        quote,
    ]
    text = render(content, citations, lambda ref: "E1")
    # Tags are numbered in the order they appear; the paraphrase and the assessment both rest on the quote.
    assert text == ('<bond:cite id="c1" kind="quote" from="E1">Leak for 8 weeks.</bond:cite> '
                    '<bond:cite kind="paraphrase" from="#c1">Notice ignored.</bond:cite> '
                    '<bond:cite kind="assessment" from="#c1" assessor="reducto">Sure.</bond:cite>')
    parsed = parse(text)
    assert parsed.content == content and [t.sources for t in parsed.tags] == [["E1"], ["#c1"], ["#c1"]]


def test_an_assessment_rests_on_text_in_the_document(bond, case):
    tools = Tools(bond, ALLOW_GAPS)  # assessments aren't an agent's to write; the round trip is under test
    draft = (f'<bond:cite id="q" kind="quote" from="{cite(tools, FIRST)}">{FIRST}</bond:cite> '
             '<bond:cite kind="assessment" from="#q" assessor="reducto">Sure.</bond:cite>\n')
    written(tools.write("graded", draft))
    version = head(bond, "graded")
    quote, assessment = bond.citations(version)
    assert assessment.supports == [Ref(version, quote.claim)]
    assert assessment.id in [c.id for c in bond.cited_by(Ref(version, quote.claim))]
    reading = tools.draft("graded")
    again = written(tools.write("graded", reading.text, reading.base))
    assert (head(bond, "graded"), again.advanced) == (version, False)
    # There is no way to name a citation itself.
    [issue] = refused(tools.write("graded", reading.text.replace("#c1", "@c1"), reading.base, dry_run=True))
    assert "'@c1' isn't a handle" in issue


# --- why ------------------------------------------------------------------------------------


def test_trace_follows_a_span_down_to_the_page_with_handles_to_cite_along_the_way(bond, case):
    tools = Tools(bond)
    written(tools.write("findings", summary(tools)))
    [mine] = tools.search(SECOND, document="findings")
    why = tools.trace(mine.handle)
    assert (why.status, why.issues, why.truncated, why.frontier) == (SupportStatus.SUPPORTED, [], False, [])
    note, ocr, page = why.spans
    assert note.handle == mine.handle and note.documents == ["findings"] and note.text == SECOND
    assert [(r.kind, r.handles) for r in note.rests_on] == [(CitationKind.QUOTE, [ocr.handle])]
    assert ocr.handle == cite(tools, SECOND)  # the evidence beneath, citable directly
    assert [(r.kind, r.attrs, r.handles) for r in ocr.rests_on] == [
        (CitationKind.TRANSCRIPTION, {"transcriber": "reducto"}, [page.handle])]
    assert page.text is None and page.where == "page 2"
    assert [(r.kind, r.handles) for r in page.rests_on] == [(CitationKind.SOURCE, [])]
    assert note.undeclared == [] and bond.verify(head(bond, "findings")) == []


def test_trace_is_bounded_and_says_where_it_stopped(bond, case):
    tools = Tools(bond)
    out = written(tools.write("findings", summary(tools)))
    whole = tools.trace(out.base)
    cut = tools.trace(out.base, limit=2)
    assert not whole.truncated and len(whole.spans) > 2
    assert cut.truncated and [s.handle for s in cut.spans] == [s.handle for s in whole.spans[:2]]
    # What the limit cut off is still named, so it can be asked about: the next span is one of them.
    named = {h for s in cut.spans for r in s.rests_on for h in r.handles}
    assert whole.spans[2].handle in named
    texts = [s.text for s in whole.spans if s.text is not None]  # a region of a page has none
    assert max(map(len, texts)) <= TRACE_TEXT


def test_trace_reports_status_and_where_support_falls_short(bond, case):
    tools = Tools(bond)
    out = written(tools.write("findings", summary(tools)))  # its heading is unsourced
    whole = tools.trace(out.base)
    assert whole.status is SupportStatus.PARTIALLY_SUPPORTED
    [heading] = tools.search("Findings", document="findings")
    assert heading.support is SupportStatus.UNSUPPORTED and heading.kinds == [CitationKind.UNSOURCED]
    assert Shortfall(SupportReason.UNSOURCED, heading.handle) in whole.issues
    assert tools.trace(heading.handle).status is SupportStatus.UNSUPPORTED


def test_trace_refuses_a_handle_that_names_nothing(bond, case):
    with pytest.raises(ToolError, match="search"):
        Tools(bond).trace("nothing@1-2")
