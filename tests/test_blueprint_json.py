import json
import pytest

from agents.blueprint import _extract_json

VALID = {
    "rooms": [{"name": "BEDROOM 1", "room_type": "bedroom"}],
    "dimensions": [],
    "raw_analysis": "ok",
}


def test_plain_json():
    assert _extract_json(json.dumps(VALID)) == VALID


def test_json_fenced():
    text = "```json\n" + json.dumps(VALID) + "\n```"
    assert _extract_json(text) == VALID


def test_prose_before_json():
    text = "Here is the blueprint analysis:\n" + json.dumps(VALID)
    assert _extract_json(text) == VALID


def test_prose_before_and_after_json():
    text = (
        "Some explanation before the JSON and explanation after it:\n"
        + json.dumps(VALID)
        + "\nThat is all."
    )
    assert _extract_json(text) == VALID


def test_json_with_surrounding_fences_and_prose():
    text = (
        "Result:\n```json\n"
        + json.dumps(VALID)
        + "\n```\nHope that helps."
    )
    assert _extract_json(text) == VALID


def test_uppercase_json_tag():
    text = "```JSON\n" + json.dumps(VALID) + "\n```"
    assert _extract_json(text) == VALID


def test_json_tag_with_space():
    text = "``` json\n" + json.dumps(VALID) + "\n```"
    assert _extract_json(text) == VALID


def test_four_backticks():
    text = "````json\n" + json.dumps(VALID) + "\n````"
    assert _extract_json(text) == VALID


def test_closing_fence_without_opening():
    text = json.dumps(VALID) + "\n```"
    assert _extract_json(text) == VALID


def test_missing_closing_fence():
    text = "```json\n" + json.dumps(VALID)
    assert _extract_json(text) == VALID


def test_empty_cases():
    cases = [
        '{"rooms":[],"dimensions":[]}',
        'Here is the blueprint analysis:\n{"rooms":[],"dimensions":[]}',
        'Some explanation before the JSON and explanation after it:\n{"rooms":[],"dimensions":[]}',
    ]
    for c in cases:
        parsed = _extract_json(c)
        assert parsed == {"rooms": [], "dimensions": []}


def test_whitespace_and_newlines_around_json():
    cases = [
        '   \n  ' + json.dumps(VALID) + '   \n  ',
        '\n\n\n' + json.dumps(VALID) + '\n\n\n',
        ' ' + json.dumps(VALID) + ' ',
    ]
    for c in cases:
        assert _extract_json(c) == VALID


def test_malformed_json_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("this is not json at all")


def test_empty_string_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("")


def test_none_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json(None)
