"""Reading a model's JSON answer.

The ESG pipeline is a line-for-line port of a PHP calculator that read its answers with
eval(), and the port kept ast.literal_eval to match it exactly -- including the part where
an answer containing JSON's true, false or null is not Python and so was skipped entirely.
That was harmless while no prompt asked for a boolean.

The client's prompts do: CLASSIFY_GUIDE section 17 returns contains_quantitative_evidence
and two more booleans, and SCORE_GUIDE section 30 returns evidence_found_on_page. Under
literal_eval every one of those answers is thrown away -- classification fails on every
page and no KPI is ever scored.

So an answer is read as JSON first, which is what the model is asked for, and only then as
a Python literal. The fallback matters: results stored before this were written with
str(dict) in places, which is not valid JSON but is a valid Python literal.
"""
import ast
import json


def parse_answer(text):
    """The answer as a Python object, or None when it cannot be read.

    Accepts what the model returns (JSON, including true/false/null) and what older stored
    results hold (a Python literal). Returns None rather than raising, because a single
    unreadable page must not end an analysis."""
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    body = text.strip()
    if not body:
        return None
    # Models fence JSON in ```json ... ``` often enough to be worth undoing here rather
    # than in every caller.
    if body.startswith("```"):
        body = body.split("\n", 1)[-1] if "\n" in body else body
        body = body.rsplit("```", 1)[0].strip()
    for read in (json.loads, ast.literal_eval):
        try:
            return read(body)
        except Exception:
            continue
    return None
