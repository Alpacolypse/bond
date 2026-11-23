import json

import pytest
from fastapi.testclient import TestClient

from bond import Bond, Citation, CitationKind, Format, Ref, TextLocator, details
from bond.http.server import create_app
from bond.workspaces import Fixed
from case_001 import SUMMARY, build_citations, claim_locator
from conftest import written


@pytest.fixture
def docs(bond, case):
    """Case 001's documents, plus its summary: document -> version."""
    summary = bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN).version
    return {**case, "summary": summary}


@pytest.fixture
def client(bond, docs):
    with TestClient(create_app(offered(bond), static_dir=None)) as c:
        yield c


def offered(*bonds: Bond) -> Fixed:
    """These workspaces, offered by their names."""
    return Fixed({b.workspace.name: b for b in bonds})


def loc(d: dict) -> str:
    return json.dumps(d)


def test_versions(bond, client, docs):
    vs = client.get("/api/versions").json()
    assert [v["version"] for v in vs] == [docs[k] for k in ("pdf", "fax", "policy", "summary")]
    pdf = details.version(bond, docs["pdf"])
    assert vs[0] == {
        "version": docs["pdf"], "content_hash": pdf.content_hash, "name": "fax_case_001", "title": None,
        "documents": ["fax_case_001"], "format": "pdf", "created_at": pdf.created_at, "version_index": 1,
        "version_count": 1, "latest": True,
    }


def test_version_detail_and_blob(client, docs):
    md = client.get(f"/api/versions/{docs['summary']}").json()
    assert md["content"] == SUMMARY and "page_count" not in md
    assert [s["version"] for s in md["siblings"]] == [docs["summary"]]
    pdf = client.get(f"/api/versions/{docs['pdf']}").json()
    assert pdf["content"] is None

    r = client.get(f"/api/versions/{docs['pdf']}/blob")
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF-")
    r = client.get(f"/api/versions/{docs['summary']}/blob")
    assert r.headers["content-type"] == "text/markdown; charset=utf-8" and r.text == SUMMARY
    assert client.get(f"/api/versions/{docs['fax']}/blob").headers["content-type"] == "application/json"


def test_unknown_version_is_404(client):
    for path in ("/api/versions/nope", "/api/versions/nope/blob", "/api/versions/nope/citations",
                 "/api/versions/nope/annotations", "/api/cited-by?version=nope", "/api/trace?version=nope",
                 "/api/text?version=nope", "/api/search?q=x&documents=nope"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert "nope" in r.json()["detail"]


def test_bad_params_are_400(client, docs):
    fax = docs["fax"]
    for path in (f"/api/text?version={fax}&locator=notjson",
                 f"/api/text?version={fax}&locator=" + json.dumps("text"),
                 f"/api/text?version={fax}&locator=" + loc({"type": "text", "start": 0, "end": 1}),
                 f"/api/cited-by?version={fax}&locator=" + loc({"type": "citation", "id": "a" * 64}),
                 f"/api/trace?version={fax}&max_depth=-1",
                 f"/api/trace?version={fax}&max_depth=abc",
                 "/api/search?q=x&limit=0",
                 "/api/search?q=",
                 "/api/search"):
        r = client.get(path)
        assert r.status_code == 400, path
        assert isinstance(r.json()["detail"], str)
    assert client.get("/api/nothing").status_code == 404


def test_citations(client, docs):
    cs = client.get(f"/api/versions/{docs['fax']}/citations").json()
    assert len({c["id"] for c in cs}) == len(cs) and all(len(c["id"]) == 64 for c in cs)
    t = next(c for c in cs if c["kind"] == "transcription")
    assert t["owner_version"] == docs["fax"] and t["attrs"] == {"transcriber": "reducto"}
    assert t["claim"]["type"] == "json" and isinstance(t["claim_text"], str)
    [s] = t["supports"]
    assert s["version"] == docs["pdf"] and s["name"] == "fax_case_001" and s["format"] == "pdf"
    assert s["locator"]["type"] == "pdf" and s["text"] is None
    a = next(c for c in cs if c["kind"] == "assessment")
    [judged] = a["supports"]
    transcribed = {c["claim"]["pointer"]: c["claim_text"] for c in cs if c["kind"] == "transcription"}
    assert judged["version"] == docs["fax"] and judged["locator"]["pointer"].endswith("/content")
    assert judged["text"] == transcribed[judged["locator"]["pointer"]]  # the text the confidence judges


def test_json_values_are_named_by_pointer_here_though_stored_by_position(bond, client, docs):
    fax = docs["fax"]
    t = next(c for c in client.get(f"/api/versions/{fax}/citations").json() if c["kind"] == "transcription")
    assert set(t["claim"]) == {"type", "pointer"} and t["claim"]["pointer"].startswith("/result/chunks/")
    assert client.get(f"/api/text?version={fax}&locator=" + loc(t["claim"])).json() == {"text": t["claim_text"]}
    # The stored form is the library's: here a value is named only by pointer, which fails in the wrong version.
    stored = details.locate_pointer(bond, fax, t["claim"]["pointer"]).locator.to_dict()
    path = stored["path"]
    assert stored["type"] == "json" and "pointer" not in stored
    assert isinstance(path, list) and all(isinstance(n, int) for n in path)
    refused = client.get(f"/api/text?version={fax}&locator=" + loc(stored))
    assert refused.status_code == 400 and '"pointer"' in refused.json()["detail"]
    missing = client.get(f"/api/text?version={fax}&locator=" + loc({"type": "json", "pointer": "/nope"}))
    assert missing.status_code == 400 and "does not exist" in missing.json()["detail"]


def test_cited_by(client, docs):
    pdf, fax = docs["pdf"], docs["fax"]
    everything = client.get(f"/api/cited-by?version={pdf}").json()
    assert everything and {c["owner_version"] for c in everything} == {fax}
    page = loc({"type": "pdf", "page": 1, "bbox": [0, 0, 1, 1]})
    some = client.get(f"/api/cited-by?version={pdf}&locator={page}").json()
    assert 0 < len(some) < len(everything)
    t = next(c for c in client.get(f"/api/versions/{fax}/citations").json() if c["kind"] == "transcription")
    by = client.get(f"/api/cited-by?version={fax}&locator=" + loc(t["claim"])).json()
    assert [c["kind"] for c in by if c["owner_version"] == fax] == ["assessment"]


def test_trace(client, docs):
    summary = docs["summary"]
    item = claim_locator(SUMMARY, "No significant damage to other rooms.").to_dict()
    g = client.get(f"/api/trace?version={summary}&locator={loc(item)}").json()
    nodes = {n["id"]: n for n in g["nodes"]}
    root = nodes[g["root"]]
    assert root["locator"] == item and root["text"] == "No significant damage to other rooms."
    assert root["name"] == "summary" and root["format"] == "markdown" and root["kinds"] == ["quote"]
    kinds = [e["kind"] for e in g["edges"]]
    assert kinds == ["quote", "transcription", "source"]
    assert g["edges"][-1]["to"] == [] and all(t in nodes for e in g["edges"] for t in e["to"])
    one = client.get(f"/api/trace?version={summary}&locator={loc(item)}&max_depth=1").json()
    assert len(one["edges"]) == 1


def test_graph(client, docs):
    g = client.get("/api/graph").json()
    nodes = {n["version"]: n for n in g["nodes"]}
    pdf = nodes[docs["pdf"]]
    assert pdf["is_source"] and pdf["latest"] and "sourcing" not in pdf
    fax = nodes[docs["fax"]]
    assert fax["citation_count"] > fax["self_citation_count"] > 0 and not fax["is_source"]
    edge = next(e for e in g["edges"] if e["from"] == docs["fax"])
    assert edge["id"] == f"{edge['from']}->{edge['to']}" and edge["count"] == sum(edge["kinds"].values())
    assert all(e["from"] != e["to"] for e in g["edges"])


def test_search_and_text(client, docs):
    hits = client.get("/api/search?q=other rooms").json()
    # The summary's quote, the OCR's transcribed block, and the parse's unsourced copies of its text.
    assert sorted({tuple(h["kinds"]) for h in hits}) == [("quote",), ("transcription",), ("unsourced",)]
    assert {"ref", "documents", "format", "snippet", "context", "kinds", "declarations"} <= set(hits[0])
    unsourced = client.get("/api/search?q=synthetic-0001").json()
    assert [h["kinds"] for h in unsourced] == [["unsourced"]]
    only = client.get("/api/search?q=other rooms&documents=summary&limit=1").json()
    [h] = only
    assert h["format"] == "markdown" and h["ref"]["version"] == docs["summary"]
    r = client.get(f"/api/text?version={h['ref']['version']}&locator={loc(h['ref']['locator'])}").json()
    assert r["text"].lower() == "other rooms"
    pdf = client.get(f"/api/text?version={docs['pdf']}").json()
    assert pdf == {"text": None}


def test_cors_allows_local_dev(client):
    r = client.get("/api/versions", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = client.get("/api/versions", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_serves_built_ui(bond, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("js")
    with TestClient(create_app(offered(bond), static_dir=dist)) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/assets/app.js").text == "js"
        assert c.get("/graph/abc").text == "<html>app</html>"
        assert c.get("/api/versions").json() == []
        assert c.get("/api/nothing").status_code == 404


def test_the_ui_never_serves_a_file_outside_its_directory(bond, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (tmp_path / "secret").write_text("TOP SECRET")
    with TestClient(create_app(offered(bond), static_dir=dist)) as c:
        # Encoded, the dots reach the server as they were sent (a client resolves a bare /../ itself).
        for path in ("/%2e%2e/secret", "/assets/%2e%2e/%2e%2e/secret", "/assets/..%2f..%2fsecret"):
            r = c.get(path)
            assert r.text == "<html>app</html>" and "TOP SECRET" not in r.text, path


def test_documents_are_listed_by_name_filtered_and_paged(bond, client, docs):
    page = client.get("/api/documents").json()
    assert page["total"] == 4 and page["offset"] == 0
    assert [d["name"] for d in page["documents"]] == ["fax_case_001", "fax_case_001.reducto", "policy_ip112", "summary"]
    summary = page["documents"][-1]
    assert summary["id"].startswith("doc_") and summary["head"] == docs["summary"]
    assert (summary["format"], summary["version_count"]) == ("markdown", 1)
    assert summary["updated_at"] == bond.history("summary")[-1].at
    fax = client.get("/api/documents?q=FAX").json()
    assert fax["total"] == 2 and all("fax" in d["name"] for d in fax["documents"])
    second = client.get("/api/documents?offset=1&limit=1").json()
    assert second["total"] == 4 and [d["name"] for d in second["documents"]] == ["fax_case_001.reducto"]
    for bad in ("offset=-1", "limit=0", "limit=501"):
        assert client.get(f"/api/documents?{bad}").status_code == 400, bad
    hits = client.get("/api/search?q=other rooms&documents=summary").json()
    assert hits and all(h["documents"] == ["summary"] for h in hits)


def test_overview_and_the_whole_graph_limit(bond, docs):
    with TestClient(create_app(offered(bond), static_dir=None, graph_limit=3)) as c:
        assert c.get("/api/overview").json() == {"workspace": bond.workspace.to_dict(), "version_count": 4,
                                                 "document_count": 4, "outside_count": 0, "graph_limit": 3}
        r = c.get("/api/graph")
        assert r.status_code == 400 and "/api/neighborhood" in r.json()["detail"]
    with TestClient(create_app(offered(bond), static_dir=None, graph_limit=4)) as c:
        assert len(c.get("/api/graph").json()["nodes"]) == 4


def test_neighborhood(client, docs):
    # summary -> fax OCR (quotes) -> pdf (transcriptions); summary -> policy (paraphrase)
    one = client.get(f"/api/neighborhood?version={docs['fax']}&depth=1").json()
    assert one["center"] == docs["fax"] and one["depth"] == 1 and not one["truncated"]
    assert {n["version"] for n in one["nodes"]} == {docs["fax"], docs["pdf"], docs["summary"]}
    assert {(e["from"], e["to"]) for e in one["edges"]} == {(docs["fax"], docs["pdf"]), (docs["summary"], docs["fax"])}
    whole = {n["version"]: n for n in client.get("/api/graph").json()["nodes"]}
    for n in one["nodes"]:  # nodes are the whole graph's nodes
        assert n == whole[n["version"]]
    two = client.get(f"/api/neighborhood?version={docs['fax']}&depth=2").json()
    assert {n["version"] for n in two["nodes"]} == set(docs.values())
    capped = client.get(f"/api/neighborhood?version={docs['fax']}&depth=2&limit=2").json()
    assert capped["truncated"] and len(capped["nodes"]) == 2 and capped["nodes"][0]["version"] == docs["fax"]
    assert [n["version"] for n in client.get(f"/api/neighborhood?version={docs['pdf']}&depth=0").json()["nodes"]] == [
        docs["pdf"]]
    for bad in ("depth=-1", "depth=6", "limit=0"):
        assert client.get(f"/api/neighborhood?version={docs['fax']}&{bad}").status_code == 400, bad
    assert client.get("/api/neighborhood?version=nope").status_code == 404


def test_an_unbuilt_ui_says_how_to_build_it(bond, tmp_path):
    with TestClient(create_app(offered(bond), static_dir=tmp_path / "missing")) as c:
        r = c.get("/")
        assert r.status_code == 503 and "npm run build" in r.text
        assert c.get("/api/overview").status_code == 200


def test_the_packaged_ui_is_served_when_built(bond):
    from bond.http.server import STATIC

    if not (STATIC / "index.html").is_file():
        pytest.skip("the UI isn't built (cd ui && npm run build)")
    with TestClient(create_app(offered(bond))) as c:
        r = c.get("/")
        assert r.status_code == 200 and '<div id="root">' in r.text


def test_a_workspace_shows_what_it_imported_and_what_stayed_outside(tmp_path):
    with Bond.local(tmp_path / "policies") as policies, Bond.local(tmp_path / "case-001") as case:
        policy = policies.ingest("policy", b"Six weeks of written notice.\n", format=Format.MARKDOWN).version
        text = "Six weeks"
        summary = policies.write_document(
            "criteria",
            text.encode(),
            [
            Citation(CitationKind.QUOTE, TextLocator(0, len(text)), [Ref(policy, TextLocator(0, len(text)))]),
        ],
            format=Format.MARKDOWN,
        ).version
        case.import_document(policies, "criteria")
        with TestClient(create_app(offered(case), static_dir=None)) as c:
            overview = c.get("/api/overview").json()
            graph = c.get("/api/graph").json()
            hood = c.get(f"/api/neighborhood?version={summary}").json()
            [cited] = c.get(f"/api/versions/{summary}/citations").json()
            trace = c.get(f"/api/trace?version={summary}").json()
            [listed] = c.get("/api/documents").json()["documents"]
            missing = c.get(f"/api/versions/{policy}")
    assert overview["workspace"]["name"] == "case-001" and overview["outside_count"] == 1
    assert {(n["version"], n["outside"]) for n in graph["nodes"]} == {(summary, False), (policy, True)}
    [outside] = [n for n in graph["nodes"] if n["outside"]]
    assert outside["format"] is None and outside["documents"] == [] and outside["name"] == policy[:12]
    assert [(e["from"], e["to"]) for e in graph["edges"]] == [(summary, policy)]
    assert {n["version"] for n in hood["nodes"]} == {summary, policy}
    assert cited["supports"][0] | {"locator": None} == {
        "version": policy, "locator": None, "name": policy[:12], "format": None, "text": None, "outside": True}
    assert [n["outside"] for n in trace["nodes"]] == [False, True]
    assert listed["imported_from"] == "policies"
    assert missing.status_code == 404


def test_provenance_api_uses_display_refs_in_search_and_provenance(bond):
    from bond.authoring import Tools

    tools = Tools(bond)
    source = bond.ingest("source", b'{"x":"alpha beta"}', format=Format.JSON)
    [hit] = tools.search("alpha")
    written(tools.write("mixed", '{"x": "<bond:cite kind=\'quote\' from=\'' + hit.handle +
                        '\'>alpha</bond:cite> <bond:cite kind=\'unsourced\'>beta</bond:cite>"}', format=Format.JSON))
    with TestClient(create_app(offered(bond), static_dir=None)) as client:
        # Search returns every match, with its direct declarations; check-support judges ancestry.
        [found] = client.get("/api/search", params={"q": "alpha beta", "documents": "mixed"}).json()
        p = found["declarations"]
        assert p["undeclared"] == []
        assert {c["kind"] for c in p["citations"]} == {"quote", "unsourced"}
        unsourced = next(c for c in p["citations"] if c["kind"] == "unsourced")
        assert unsourced["claim"] == {"type": "json", "pointer": "/x", "start": 6, "end": 10}
        mixed = bond.document("mixed").head
        response = client.get("/api/declarations", params={
            "version": mixed, "locator": json.dumps(found["ref"]["locator"]),
        })
        assert response.status_code == 200 and response.json() == p
        assert client.get("/api/declarations", params={"version": source.version}).json()["undeclared"] == []
        assert client.get("/api/declarations?version=unknown").status_code == 404
        assert client.get("/api/declarations", params={"version": mixed, "locator": "bad"}).status_code == 400


def test_support_endpoint_has_display_regions_paths_and_validated_enum(bond):
    from bond import ALLOW_GAPS, Citation, CitationKind, Format, JsonLocator

    v = bond.write_document(
        "mixed",
        b'{"x":"alpha beta"}',
        [
        Citation(CitationKind.SOURCE, JsonLocator((0,), (0, 5)), []),
    ],
        format=Format.JSON,
        policy=ALLOW_GAPS,
    ).version
    with TestClient(create_app(offered(bond), static_dir=None)) as client:
        r = client.get("/api/check-support", params={"version": v})
        assert r.status_code == 200
        report = r.json()
        assert report["status"] == "partially_supported"
        assert set(report) == {"ref", "status", "regions", "issues"}
        assert set(report["issues"][0]) == {"ref", "reason", "path"}
        assert report["regions"][1] == {"ref": {"version": v,
            "locator": {"type": "json", "pointer": "/x", "start": 6, "end": 10}}, "status": "unsupported"}
        assert report["issues"][0]["ref"] == report["regions"][1]["ref"]
        assert report["issues"][0]["path"][0] == report["ref"]
        for kwargs in ({"limit": 0}, {"max_depth": -1}):
            assert client.get("/api/check-support", params={"version": v, **kwargs}).status_code == 400
        assert client.get("/api/check-support?version=missing").status_code == 404


def test_workspace_selection_scopes_every_data_route(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with Bond.local(tmp_path / "case-a") as a, Bond.local(tmp_path / "case-b") as b:
        av = a.ingest("record-a", b"Alice only", format=Format.MARKDOWN).version
        bv = b.ingest("record-b", b"Bob only", format=Format.MARKDOWN).version
        app = create_app(offered(a, b), static_dir=None)
        with TestClient(app) as client:
            stores = client.get("/api/workspaces").json()
            assert [(s["name"], s["id"]) for s in stores] == [("case-a", a.workspace.id), ("case-b", b.workspace.id)]
            assert client.get("/api/overview").json()["workspace"]["id"] == a.workspace.id
            for bond, version, other, phrase in ((a, av, bv, "Bob"), (b, bv, av, "Alice")):
                params = {"workspace": bond.workspace.name}
                assert client.get("/api/overview", params=params).json()["workspace"]["id"] == bond.workspace.id
                assert [n["version"] for n in client.get("/api/graph", params=params).json()["nodes"]] == [version]
                assert [d["head"] for d in client.get("/api/documents", params=params).json()["documents"]] == [version]
                assert [v["version"] for v in client.get("/api/versions", params=params).json()] == [version]
                assert client.get("/api/search", params={**params, "q": phrase}).json() == []
                for suffix in ("", "/blob", "/citations", "/annotations"):
                    assert client.get(f"/api/versions/{version}{suffix}", params=params).status_code == 200
                    assert client.get(f"/api/versions/{other}{suffix}", params=params).status_code == 404
                for route in ("neighborhood", "cited-by", "trace", "declarations", "check-support", "text"):
                    assert client.get(f"/api/{route}", params={**params, "version": version}).status_code == 200
                    assert client.get(f"/api/{route}", params={**params, "version": other}).status_code == 404
            assert client.get("/api/graph?workspace=missing").status_code == 404
            assert client.get("/api/overview?workspace=../unlisted").status_code == 404
            assert client.get(f"/api/overview?workspace={a.workspace.id}").status_code == 404  # by name, not id
            # Tabs and concurrent callers cannot change each other's selected workspace.
            names = ["case-a", "case-b"] * 5
            with ThreadPoolExecutor() as pool:
                found = list(pool.map(lambda name: client.get("/api/overview", params={"workspace": name}).json()
                                      ["workspace"]["name"], names))
            assert found == names
            assert client.get("/api/overview").json()["workspace"]["id"] == a.workspace.id


def test_what_is_offered_is_read_on_every_request(tmp_path):
    with Bond.local(tmp_path / "case-a") as a, Bond.local(tmp_path / "case-b") as b:
        offering: dict[str, Bond] = {"case-a": a}
        with TestClient(create_app(Fixed(offering), static_dir=None)) as client:
            before = client.get("/api/overview", params={"workspace": "case-b"}).status_code
            offering["case-b"] = b  # granted while the app runs
            listed = [w["name"] for w in client.get("/api/workspaces").json()]
            after = client.get("/api/overview", params={"workspace": "case-b"}).json()["workspace"]["name"]
            del offering["case-a"], offering["case-b"]  # revoked
            empty = client.get("/api/workspaces").json(), client.get("/api/overview").status_code
    assert (before, listed, after, empty) == (404, ["case-a", "case-b"], "case-b", ([], 404))
