"""Legacy monolithic sales report module.

Everything (model, totals, rendering) lives in this one file and it has grown
unwieldy. It also crashes on an empty report. See README.md for the refactor
that is required.
"""

from dataclasses import dataclass, field


@dataclass
class Line:
    sku: str
    qty: int
    unit_price: float
    discount_pct: float = 0.0


@dataclass
class Report:
    title: str
    lines: list = field(default_factory=list)


def line_net(line):
    gross = line.qty * line.unit_price
    return gross - gross * line.discount_pct / 100.0


def summarize(report):
    total = 0.0
    for ln in report.lines:
        total += line_net(ln)
    # BUG: divides by len(lines) unconditionally -> ZeroDivisionError on empty.
    average = total / len(report.lines)
    return {
        "title": report.title,
        "count": len(report.lines),
        "total": round(total, 2),
        "average": round(average, 2),
    }


def render_report(report):
    data = summarize(report)
    out = [
        "# " + data["title"],
        "",
        "- lines: %d" % data["count"],
        "- total: %.2f" % data["total"],
        "- average: %.2f" % data["average"],
        "",
    ]
    for ln in report.lines:
        out.append("| %s | %d | %.2f |" % (ln.sku, ln.qty, line_net(ln)))
    return "\n".join(out) + "\n"
