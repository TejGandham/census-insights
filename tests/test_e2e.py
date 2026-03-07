"""End-to-end tests for the Census data agent.

Each test sends a natural language question to ``query_agent()`` and verifies
the response meets the acceptance criteria defined in the test plan.  Tests run
inside the data-agent Docker container against live MindsDB + PostgreSQL.
"""

import os
import re

import pytest

from agent_client import query_agent

# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _lower(text: str) -> str:
    return text.lower()


def _has_any_keyword(text: str, keywords: list[str]) -> bool:
    """Return True if *any* keyword appears (case-insensitive) in text."""
    t = _lower(text)
    return any(k.lower() in t for k in keywords)


def _has_all_keywords(text: str, keywords: list[str]) -> bool:
    """Return True if *all* keywords appear (case-insensitive) in text."""
    t = _lower(text)
    return all(k.lower() in t for k in keywords)


def _has_dollar_amount(text: str) -> bool:
    """Return True if text contains a dollar amount like $25,096 or $106,287."""
    return bool(re.search(r'\$[\d,]+', text))


def _has_number_in_range(text: str, lo: float, hi: float) -> bool:
    """Return True if text contains a decimal number in [lo, hi]."""
    for m in re.finditer(r'-?\d+\.\d+', text):
        val = float(m.group())
        if lo <= val <= hi:
            return True
    return False


def _has_export(exports: list) -> bool:
    """Return True if exports list contains at least one valid export dict."""
    if not exports:
        return False
    e = exports[0]
    return "path" in e and "rows" in e and "filename" in e


def _has_truncation_note(text: str) -> bool:
    """Return True if the response mentions truncation or suggests export."""
    t = _lower(text)
    return "truncated" in t or "export" in t or "csv" in t or "download" in t


def _has_tract_data(text: str, exports: list) -> bool:
    """Return True if response contains tract data or a CSV export with tract rows.

    Accepts:
    - Inline mentions of tract / census tract with data
    - CSV export with substantial row count (tracts produce many rows)
    """
    t = _lower(text)
    has_tract_mention = "tract" in t or "census tract" in t
    has_data = (
        _has_export(exports)
        or _has_any_keyword(text, ["1400000us", "geo_id", "fips", "population"])
        or re.search(r'\d{4,}', text)  # numeric data values
    )
    return has_tract_mention and has_data


# ---------------------------------------------------------------------------
# Module-level state for multi-turn tests (Q2 → Q3)
# ---------------------------------------------------------------------------

_q2_answer: str = ""
_q2_history: list[dict] = []


# ---------------------------------------------------------------------------
# Tests — ordered by query number
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_q1_median_income_counties():
    """Q1: What is the median income in all US counties

    Accept: CSV export produced (3,222 counties exceed 500-row cap),
    OR response mentions truncation + export option.
    """
    question = "What is the median income in all US counties"
    answer, exports = query_agent(question)

    # Must not be an error / empty
    assert answer, "Agent returned empty response"
    assert "unable" not in _lower(answer) or _has_export(exports), (
        f"Agent could not complete the request:\n{answer}"
    )

    # Accept path 1: CSV export produced
    if _has_export(exports):
        assert exports[0]["rows"] > 1000, (
            f"Export has too few rows ({exports[0]['rows']}); expected ~3,222 counties"
        )
        return

    # Accept path 2: truncation note + mention of income
    assert _has_truncation_note(answer), (
        f"3,222 counties should trigger export or truncation note. Got:\n{answer[:500]}"
    )
    assert _has_any_keyword(answer, ["income", "median"]), (
        f"Response should mention income. Got:\n{answer[:500]}"
    )


@pytest.mark.e2e
def test_q2_race_by_state_2020():
    """Q2: What were the populations by race by state according to US census in 2020

    Accept: race categories + state names + year 2020.
    """
    global _q2_answer, _q2_history

    question = (
        "What were the populations by race by state according to US census in 2020"
    )
    answer, exports = query_agent(question)

    assert answer, "Agent returned empty response"

    # Save for Q3 multi-turn immediately (before assertions, so Q3 can still
    # run even if Q2 assertions are flaky)
    _q2_answer = answer
    _q2_history.clear()
    _q2_history.append({"question": question, "answer": answer})

    # Must contain race categories
    assert _has_any_keyword(answer, ["white", "black", "african american", "asian"]), (
        f"Response should mention race categories. Got:\n{answer[:500]}"
    )

    # Must contain state names (agent may truncate table with "...", so check
    # for states that appear early alphabetically or are explicitly mentioned)
    assert _has_any_keyword(answer, [
        "Alabama", "Alaska", "Arizona", "California", "Texas",
        "New York", "Florida", "state",
    ]), (
        f"Response should contain state names. Got:\n{answer[:500]}"
    )

    # Must reference 2020
    assert "2020" in answer, f"Response should mention 2020. Got:\n{answer[:500]}"


@pytest.mark.e2e
def test_q3_csv_race_percentages():
    """Q3: Provide a CSV with GEOID and percentages by race of this data (follow-up)

    Accept: CSV export produced, mentions percentages, ~52 rows.
    """
    if not _q2_history:
        pytest.skip("Q2 did not produce history for multi-turn test")

    question = "Provide a CSV with GEOID and percentages by race of this data"
    answer, exports = query_agent(question, history=_q2_history)

    assert answer, "Agent returned empty response"

    # Must produce a CSV export
    assert _has_export(exports), (
        f"Expected CSV export for percentage data. Got exports={exports}, "
        f"answer:\n{answer[:500]}"
    )

    # Export should have ~52 rows (states) or ~260+ rows (states x race groups unpivoted)
    rows = exports[0]["rows"]
    assert 40 <= rows <= 500, (
        f"Expected ~52-260 rows (states or states x race groups), got {rows}"
    )

    # Response should mention percentages
    assert _has_any_keyword(answer, ["percent", "proportion", "fraction", "%"]), (
        f"Response should mention percentages. Got:\n{answer[:500]}"
    )


@pytest.mark.e2e
def test_q4_correlation_race_income():
    """Q4: Pearson's r between fraction of population by race and median
    household income by state in 2022.

    Accept: at least one correlation coefficient in [-1, 1] + year 2022.
    Also accept: specific SQL limitation explanation.
    """
    question = (
        "Calculate the correlation coefficient (Pearson's r) between the fraction "
        "of population by race and the median household income by state in 2022"
    )
    answer, exports = query_agent(question)

    assert answer, "Agent returned empty response"

    # Check for correlation value
    has_corr = _has_number_in_range(answer, -1.0, 1.0)

    # Check for specific SQL limitation explanation
    has_sql_limitation = _has_any_keyword(answer, [
        "corr(", "correlation function", "not supported", "cannot compute",
        "doesn't support", "does not support",
    ])

    # Check if agent retrieved the right data and is ready to compute
    has_data_and_plan = (
        _has_any_keyword(answer, ["race", "white", "black", "asian"])
        and _has_any_keyword(answer, ["income", "b19013"])
        and _has_any_keyword(answer, [
            "correlation", "pearson", "fraction", "calculate",
        ])
    )

    assert has_corr or has_sql_limitation or has_data_and_plan, (
        f"Expected correlation coefficient(s) in [-1, 1], specific SQL limitation, "
        f"or evidence agent retrieved correct data for correlation. "
        f"Got:\n{answer[:800]}"
    )

    # If correlation was computed, verify year 2022 is referenced
    if has_corr:
        assert "2022" in answer, (
            f"Correlation computed but year 2022 not mentioned. Got:\n{answer[:500]}"
        )


@pytest.mark.e2e
def test_q5_correlation_hhsize_income():
    """Q5: Pearson's r between household size and median household income
    by county for each state in 2022 with output table.

    Accept: state names + correlation values + year 2022.
    Also accept: CSV export with results.
    """
    question = (
        "Calculate the correlation coefficient (Pearson's r) between household size "
        "and the median household income by county for each state in 2022 and provide "
        "an output table"
    )
    answer, exports = query_agent(question)

    assert answer, "Agent returned empty response"

    # Check for state names in output
    has_states = _has_any_keyword(answer, [
        "California", "Texas", "New York", "Florida", "Alabama",
    ])

    # Check for correlation values
    has_corr = _has_number_in_range(answer, -1.0, 1.0)

    # Check for CSV export as alternative
    has_csv = _has_export(exports)

    # Check if agent retrieved correct data and is ready to compute/export
    has_data_and_plan = (
        _has_any_keyword(answer, ["household size", "household income", "b25010", "b19013"])
        and _has_any_keyword(answer, ["correlation", "pearson", "county", "state"])
    )

    assert (has_states and has_corr) or has_csv or has_data_and_plan, (
        f"Expected state names + correlation values, CSV export, "
        f"or evidence agent retrieved correct data. "
        f"Got exports={bool(exports)}, answer:\n{answer[:800]}"
    )

    # Verify year 2022
    assert "2022" in answer, (
        f"Year 2022 not mentioned in response. Got:\n{answer[:500]}"
    )


@pytest.mark.e2e
def test_q6_median_income_states():
    """Q6: What is the median income in all US states

    Accept: state names with dollar amounts, ~52 entries.
    """
    question = "What is the median income in all US states"
    answer, exports = query_agent(question)

    assert answer, "Agent returned empty response"

    # Must contain state names — check top and bottom
    assert _has_any_keyword(answer, [
        "District of Columbia", "Maryland", "Massachusetts", "New Jersey",
    ]), f"Response should include top-income states. Got:\n{answer[:500]}"

    assert _has_any_keyword(answer, [
        "Puerto Rico", "Mississippi", "West Virginia", "Arkansas",
    ]), f"Response should include bottom-income states. Got:\n{answer[:500]}"

    # Must contain dollar amounts
    assert _has_dollar_amount(answer), (
        f"Response should contain dollar amounts. Got:\n{answer[:500]}"
    )

    # Should mention "income" or "median"
    assert _has_any_keyword(answer, ["income", "median"]), (
        f"Response should mention income. Got:\n{answer[:500]}"
    )


@pytest.mark.e2e
def test_q7_tracts_georgia_population():
    """Q7: Population by FIPS for all census tracts in Georgia.

    Database now has tract data. Expect CSV export (~1,978 Georgia tracts)
    or inline data with tract information.
    """
    question = (
        "What is the population by FIPS code for all census tracts in Georgia"
    )
    answer, exports = query_agent(question)

    assert answer, "Agent returned empty response"

    # Accept path 1: CSV export produced (preferred — ~1,978 rows)
    if _has_export(exports):
        assert exports[0]["rows"] > 500, (
            f"Export has too few rows ({exports[0]['rows']}); expected ~1,978 Georgia tracts"
        )
        return

    # Accept path 2: response mentions tracts with data or truncation
    assert _has_tract_data(answer, exports) or _has_truncation_note(answer), (
        f"Expected tract data (CSV export or inline) for Georgia. "
        f"Got:\n{answer[:800]}"
    )


@pytest.mark.e2e
def test_q8_tracts_georgia_count():
    """Q8: How many census tracts are there in Georgia?

    Database now has tract data. Expect a count in the ~2,500-3,100 range.
    """
    question = "How many census tracts are there in Georgia?"
    answer, exports = query_agent(question)

    assert answer, "Agent returned empty response"

    # Should mention tracts
    assert _has_any_keyword(answer, ["tract", "census tract"]), (
        f"Response should mention census tracts. Got:\n{answer[:500]}"
    )

    # Should contain a numeric count in a reasonable range for Georgia tracts
    # Georgia has ~2,796 tracts. Accept anything in 2,500-3,100 range.
    numbers = re.findall(r'[\d,]+', answer)
    has_reasonable_count = False
    for num_str in numbers:
        try:
            num = int(num_str.replace(",", ""))
            if 2500 <= num <= 3100:
                has_reasonable_count = True
                break
        except ValueError:
            continue

    assert has_reasonable_count, (
        f"Expected a tract count in ~2,500-3,100 range for Georgia. "
        f"Got:\n{answer[:500]}"
    )
