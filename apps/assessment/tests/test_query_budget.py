"""A list page must cost the same whether it shows three rows or fifty.

This is the failure that does not show up in development. `question_count` was
a `.count()` per test, so a page of fifty made fifty extra round trips and a
catalogue of a thousand would have made a thousand — invisible on a demo
database with seven rows, and the reason a page stops responding once real
data arrives.

Asserting a fixed number would break every time an unrelated join is added, so
what is pinned is the property that matters: the count does not grow with the
number of rows.
"""

import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection

from apps.assessment.models import Test as AssessmentTest
from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

TESTS_URL = "/api/v1/assessment/tests/"


@pytest.fixture
def many_tests(admin_user):
    """Twelve published tests, each with a different number of questions."""
    from apps.assessment.models import Question

    created = []
    for index in range(12):
        test = AssessmentTest.objects.create(
            title=f"Test {index}",
            author=admin_user,
            status=ModerationStatus.PUBLISHED,
            is_public=True,
        )
        for question in range(index % 4 + 1):
            Question.objects.create(
                test=test,
                text=f"Question {question}",
                type="SINGLE",
                order=question,
            )
        created.append(test)
    return created


def queries_for(client, url) -> int:
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)
        assert response.status_code == 200, response.data
    return len(captured)


def test_the_test_list_does_not_query_per_row(auth, student, many_tests):
    """The N+1 this file exists for."""
    client = auth(student)

    few = queries_for(client, f"{TESTS_URL}?page_size=2")
    many = queries_for(client, f"{TESTS_URL}?page_size=12")

    assert many <= few + 1, (
        f"{few} queries for 2 rows and {many} for 12 — the count is growing "
        "with the rows, which means a query is running inside the loop again"
    )


def test_the_question_count_is_still_correct(auth, student, many_tests):
    """Cheap is worthless if it is wrong: the annotation must match reality."""
    response = auth(student).get(f"{TESTS_URL}?page_size=12")

    by_title = {row["title"]: row["question_count"] for row in response.data["results"]}
    for test in many_tests:
        if test.title in by_title:
            assert by_title[test.title] == test.questions.count(), test.title
