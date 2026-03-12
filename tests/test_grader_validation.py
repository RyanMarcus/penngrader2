from app.core.grader_validation import expected_grader_function_name, validate_grader_source


def test_expected_name():
    assert expected_grader_function_name("problem1") == "grade_problem1"
    assert expected_grader_function_name("problem-2") == "grade_problem_2"


def test_validate_grader_source_ok():
    source = """
import math

def grade_problem1(submission, callback):
    callback('hi')
    return (1, 'ok')
"""
    fn = validate_grader_source(source, "problem1", {"math"})
    assert fn == "grade_problem1"
