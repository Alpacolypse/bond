from bond.normalize import find_all, normalize


def test_whitespace_runs_collapse_and_edges_trim():
    n = normalize("  a \n\t b c  ")
    assert n.text == "a b c"


def test_curly_quotes_and_ligatures_fold():
    assert normalize("“ﬁne” isn’t").text == '"fine" isn\'t'


def test_hyphenated_line_break_is_joined():
    assert normalize("ceil-\n  ing").text == "ceiling"
    # A hyphen that isn't at a line break, or isn't between letters, stays.
    assert normalize("well-known").text == "well-known"
    assert normalize("L4-\n5").text == "l4- 5"


def test_soft_hyphen_removed_and_case_folding_optional():
    assert normalize("Medi­ation").text == "mediation"
    assert normalize("Mediation", fold_case=False).text == "Mediation"


def test_offsets_map_back_to_raw():
    raw = "Leak: “no  response.” Then ceil-\ning."
    n = normalize(raw)
    for needle in ['"no response."', "ceiling", "leak"]:
        [(s, e)] = find_all(n, needle)
        assert normalize(raw[s:e]).text == needle
    [(s, e)] = find_all(n, "ceiling")
    assert raw[s:e] == "ceil-\ning"


def test_find_all_returns_overlapping_matches():
    assert find_all(normalize("aaaa"), "aa") == [(0, 2), (1, 3), (2, 4)]
    assert find_all(normalize("abc"), "") == []
