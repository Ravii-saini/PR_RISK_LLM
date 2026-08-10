import pytest

from worker.llm.schema import AssessmentParseError, parse_assessment


def test_parse_assessment_valid_json():
    raw = '{"risk_level": "high", "reasons": ["a", "b"], "suggested_checks": ["c"]}'
    a = parse_assessment(raw)
    assert a.risk_level == "high"
    assert a.reasons == ["a", "b"]
    assert a.suggested_checks == ["c"]
    assert a.degraded is False


def test_parse_assessment_strips_markdown_code_fence():
    raw = '```json\n{"risk_level": "low", "reasons": [], "suggested_checks": []}\n```'
    a = parse_assessment(raw)
    assert a.risk_level == "low"


def test_parse_assessment_tolerates_surrounding_prose():
    raw = 'Sure, here is my assessment:\n{"risk_level": "medium", "reasons": [], "suggested_checks": []}\nHope that helps!'
    a = parse_assessment(raw)
    assert a.risk_level == "medium"


def test_parse_assessment_missing_risk_level_raises():
    with pytest.raises(AssessmentParseError):
        parse_assessment('{"reasons": [], "suggested_checks": []}')


def test_parse_assessment_invalid_risk_level_raises():
    with pytest.raises(AssessmentParseError):
        parse_assessment('{"risk_level": "extreme", "reasons": [], "suggested_checks": []}')


def test_parse_assessment_malformed_json_raises():
    with pytest.raises(AssessmentParseError):
        parse_assessment('{"risk_level": "low", "reasons": [oops]}')


def test_parse_assessment_no_json_object_raises():
    with pytest.raises(AssessmentParseError):
        parse_assessment("I refuse to answer in JSON.")


def test_parse_assessment_defaults_missing_arrays_to_empty():
    a = parse_assessment('{"risk_level": "low"}')
    assert a.reasons == []
    assert a.suggested_checks == []


def test_parse_assessment_non_array_reasons_raises():
    with pytest.raises(AssessmentParseError):
        parse_assessment('{"risk_level": "low", "reasons": "not a list"}')
