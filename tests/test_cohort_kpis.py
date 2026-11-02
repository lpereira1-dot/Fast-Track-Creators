from datetime import date

from fast_track.dashboard.cohort_kpis import (
    cohort_month_kpis,
    format_count_delta,
    format_rate_delta,
    month_over_month_activation_table,
    month_over_month_table,
    previous_month_start,
)
from fast_track.models import Creator, GiftAward, Milestone


def _creator(cid: str, joined: str) -> Creator:
    return Creator.from_api(
        {
            "creator_id": cid,
            "name": cid,
            "email": f"{cid}@example.com",
            "joined_at": joined,
        }
    )


def test_previous_month_start():
    assert previous_month_start(date(2026, 8, 1)) == date(2026, 7, 1)
    assert previous_month_start(date(2026, 1, 1)) == date(2025, 12, 1)


def test_format_count_delta():
    assert format_count_delta(12, 10) == "+2 (+20.0%)"
    assert format_count_delta(3, 0) == "+3"


def test_format_rate_delta():
    assert format_rate_delta(50.0, 40.0) == "+10.0 pp"


def test_month_over_month_activation_table_focuses_on_posted_and_sold():
    prior = {
        "n_creators": 10,
        "n_posted": 5,
        "post_rate": 50.0,
        "n_first_sale": 2,
        "sale_rate": 20.0,
        "n_gifted": 0,
        "gift_spend": 0.0,
        "pre_posting_rate": None,
    }
    current = {
        "n_creators": 12,
        "n_posted": 9,
        "post_rate": 75.0,
        "n_first_sale": 4,
        "sale_rate": 33.3,
        "n_gifted": 0,
        "gift_spend": 0.0,
        "pre_posting_rate": None,
    }
    table = month_over_month_activation_table("July 2026", "August 2026", prior, current)
    assert list(table["Metric"]) == ["Creators admitted", "Posted (at least once)", "First sale"]
    posted = table[table["Metric"] == "Posted (at least once)"].iloc[0]
    assert posted["Change vs prior month"] == "+4 (+80.0%)"


def test_month_over_month_table_columns():
    prior = {
        "n_creators": 10,
        "n_posted": 5,
        "post_rate": 50.0,
        "n_first_sale": 2,
        "sale_rate": 20.0,
        "n_gifted": 4,
        "gift_spend": 100.0,
        "pre_posting_rate": 100.0,
    }
    current = {
        "n_creators": 12,
        "n_posted": 9,
        "post_rate": 75.0,
        "n_first_sale": 3,
        "sale_rate": 25.0,
        "n_gifted": 6,
        "gift_spend": 150.0,
        "pre_posting_rate": 100.0,
    }
    table = month_over_month_table("July 2026", "August 2026", prior, current)
    assert list(table.columns) == ["Metric", "July 2026", "August 2026", "Change"]
    post_row = table[table["Metric"] == "Post rate"].iloc[0]
    assert post_row["Change"] == "+25.0 pp"
