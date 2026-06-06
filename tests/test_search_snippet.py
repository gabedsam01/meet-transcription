from app.web.helpers import search_snippet


def test_match_in_the_middle_has_both_ellipses():
    text = ("alpha " * 60) + "TARGET" + (" omega" * 60)
    out = search_snippet(text, "target", radius=20)
    assert "TARGET" in out
    assert out.startswith("…")
    assert out.endswith("…")


def test_match_near_start_has_no_leading_ellipsis():
    out = search_snippet("TARGET word two three", "target", radius=50)
    assert out.startswith("TARGET")
    assert not out.startswith("…")


def test_query_not_found_returns_leading_characters_fallback():
    text = "alpha beta gamma " * 30
    out = search_snippet(text, "zzz", radius=20)
    assert out.startswith("alpha beta")
    assert out.endswith("…")


def test_blank_or_none_text_returns_empty_string():
    assert search_snippet("", "x") == ""
    assert search_snippet(None, "x") == ""


def test_whitespace_and_newlines_are_collapsed():
    out = search_snippet("hello\n\n   world   foo", "world", radius=50)
    assert "\n" not in out
    assert "hello world foo" in out


def test_accented_match_is_case_insensitive():
    out = search_snippet("Discutimos o Orçamento Anual", "orçamento", radius=50)
    assert "Orçamento" in out
