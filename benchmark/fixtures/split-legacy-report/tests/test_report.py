import pytest

from app.report import Line, Report, line_net, render_report, summarize


def test_summarize_totals():
    report = Report("Q3", [Line("A", 2, 10.0), Line("B", 1, 4.0, discount_pct=25)])
    assert summarize(report) == {"title": "Q3", "count": 2, "total": 23.0, "average": 11.5}


def test_summarize_empty_report_is_zero_average():
    assert summarize(Report("Empty")) == {
        "title": "Empty",
        "count": 0,
        "total": 0.0,
        "average": 0.0,
    }


def test_line_net_applies_discount():
    assert line_net(Line("A", 3, 5.0, discount_pct=10)) == pytest.approx(13.5)


def test_render_report_markdown():
    text = render_report(Report("Q3", [Line("A", 2, 10.0)]))
    assert text.splitlines()[:2] == ["# Q3", ""]
    assert "- total: 20.00" in text
    assert "| A | 2 | 20.00 |" in text
    assert text.endswith("\n")


def test_render_report_empty():
    assert "lines: 0" in render_report(Report("Empty"))


def test_model_and_render_are_split():
    from app.report import model, render

    assert hasattr(model, "summarize") and hasattr(model, "line_net")
    assert hasattr(model, "Line") and hasattr(model, "Report")
    assert hasattr(render, "render_report")
