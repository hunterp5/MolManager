"""Unit tests for table search query operators."""

from __future__ import annotations

from molmanager.ui.search_query import (
    evaluate_search_condition,
    evaluate_search_conditions,
    evaluate_search_expression,
    parse_search_condition,
    parse_search_conditions,
    parse_search_expression,
    parse_search_term_groups,
    parse_substructure_term,
    split_search_terms,
    validate_search_text_query,
)


def test_split_search_terms():
    assert split_search_terms('"a", "b" , ,"c"') == ["a", "b", "c"]


def test_parse_search_term_groups_comma_or():
    assert parse_search_term_groups('"a", "b"|"c"') == [['"a"'], ['"b"'], ['"c"']]


def test_parse_search_term_groups_quoted_comma_inside_literal():
    assert parse_search_term_groups('"a, b"') == [['"a, b"']]


def test_parse_search_term_groups_and():
    assert parse_search_term_groups(">5 & <10") == [[">5", "<10"]]


def test_parse_search_term_groups_compound():
    assert parse_search_term_groups(">5 & <10, >=1") == [[">5", "<10"], [">=1"]]


def test_parse_search_term_groups_parens():
    assert parse_search_term_groups('"(a|b)", "c"') == [['"(a|b)"'], ['"c"']]


def test_unquoted_string_rejected():
    assert parse_search_condition("ethane", partial=True) is None
    assert validate_search_text_query("ethane", partial=True) is not None


def test_quoted_string_accepted():
    cond = parse_search_condition('"ethane"', partial=True)
    assert cond is not None and cond.op == "contains" and cond.value == "ethane"


def test_numeric_comparisons():
    assert parse_search_condition(">5", partial=True) == parse_search_condition("> 5", partial=True)
    assert evaluate_search_condition("10", parse_search_condition(">5", partial=True), partial=True, case_sensitive=False)
    assert not evaluate_search_condition(
        "3", parse_search_condition(">5", partial=True), partial=True, case_sensitive=False
    )
    assert evaluate_search_condition(
        "3", parse_search_condition("<=3", partial=True), partial=True, case_sensitive=False
    )


def test_not_contains():
    cond = parse_search_condition('NOT "ethane"', partial=True)
    assert cond is not None and cond.op == "not_contains" and cond.value == "ethane"
    assert evaluate_search_condition("propane", cond, partial=True, case_sensitive=False)
    assert not evaluate_search_condition("methane", cond, partial=True, case_sensitive=False)


def test_empty_and_not_empty():
    empty = parse_search_condition("empty", partial=True)
    assert evaluate_search_condition("  ", empty, partial=True, case_sensitive=False)
    assert not evaluate_search_condition("x", empty, partial=True, case_sensitive=False)
    not_empty = parse_search_condition("not empty", partial=True)
    assert evaluate_search_condition("x", not_empty, partial=True, case_sensitive=False)


def test_and_or_combination_legacy_flat():
    conds = parse_search_conditions('>5, <10', partial=True)
    assert evaluate_search_conditions("7", conds, match_and=False, partial=True, case_sensitive=False)
    assert evaluate_search_conditions("12", conds, match_and=False, partial=True, case_sensitive=False)


def test_compound_and_or_expression():
    expr = parse_search_expression(">5 & <10, >=20", partial=True)
    assert evaluate_search_expression("7", expr, partial=True, case_sensitive=False)
    assert not evaluate_search_expression("15", expr, partial=True, case_sensitive=False)
    assert evaluate_search_expression("25", expr, partial=True, case_sensitive=False)


def test_pipe_or_expression():
    expr = parse_search_expression('"eth*"|"prop*"', partial=True)
    assert evaluate_search_expression("ethane", expr, partial=True, case_sensitive=False)
    assert evaluate_search_expression("propane", expr, partial=True, case_sensitive=False)
    assert not evaluate_search_expression("methane", expr, partial=True, case_sensitive=False)


def test_wildcard_contains():
    cond = parse_search_condition('"eth*"', partial=True)
    assert evaluate_search_condition("ethane", cond, partial=True, case_sensitive=False)
    assert not evaluate_search_condition("methane", cond, partial=True, case_sensitive=False)


def test_substructure_not_prefix():
    pat, neg = parse_substructure_term("NOT c1ccccc1")
    assert neg and pat == "c1ccccc1"
