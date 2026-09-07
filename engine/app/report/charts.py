"""Hand-written SVG for the report charts (CLAUDE.md · plan §14).

No plotting library. The output is a hexagon and some bars, WeasyPrint renders
inline SVG well, and matplotlib in the image would be 60 MB of dependency to
draw twelve polygons.

PURE, like `app/scoring/*`: numbers in, an SVG string out. No database, no
settings, no file access. That is what lets `test_charts.py` assert the geometry
arithmetically instead of rendering a PDF and looking at it.

Two rules the shapes here follow:

* **Scale to the instrument's ceiling, never to the observed maximum.** Scaling
  to what this student scored makes every profile look equally strong and hides
  a flat one — the elevation-vs-shape distinction §8 ipsatizes away for matching
  is exactly what a student needs to SEE here.
* **Colour is never the only signal.** Every bar is labelled with its own value
  and every hexagon vertex with its letter. The PDF gets printed in greyscale on
  a school photocopier, and a fair number of readers are colour-blind.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

# The interest instrument's ceiling: 10 checkbox items per RIASEC scale
# (`score_interests` — "every scale scores 0..10"). Passed in rather than read
# from a constant at the call site so a future instrument with a different
# ceiling cannot silently mis-scale the hexagon.
HEX_SIZE = 320
HEX_RADIUS = 118
HEX_LABEL_RADIUS = 143

BAR_WIDTH = 460
BAR_HEIGHT = 26
BAR_GAP = 14
BAR_LABEL_WIDTH = 170

# Brand default. `brand_hex` from the organisation overrides it at render time
# (0001_init.sql), which is what makes a report look like the school's rather
# than like ours.
DEFAULT_BRAND = "#1C6A61"
GRID = "#D6D9D8"
INK = "#1F2421"
MUTED = "#5C6663"


def hexagon(
    values: dict[str, float],
    order: tuple[str, ...],
    maximum: float,
    brand: str = DEFAULT_BRAND,
) -> str:
    """RIASEC hexagon: the student's shape over a reference grid.

    `order` fixes which vertex is which — Holland's hexagon runs R-I-A-S-E-C
    clockwise from the top, and the adjacency is the meaningful part of the
    shape (neighbouring interests are related; opposite ones are not). Passing a
    different order would draw a different claim.
    """
    centre = HEX_SIZE / 2
    brand = _colour(brand)

    rings = "".join(
        f'<polygon points="{_ring(order, centre, HEX_RADIUS * fraction)}" '
        f'fill="none" stroke="{GRID}" stroke-width="1"/>'
        for fraction in (0.25, 0.5, 0.75, 1.0)
    )

    spokes = "".join(
        f'<line x1="{_r(centre)}" y1="{_r(centre)}" '
        f'x2="{_r(x)}" y2="{_r(y)}" stroke="{GRID}" stroke-width="1"/>'
        for x, y in (_vertex(index, len(order), centre, HEX_RADIUS) for index in range(len(order)))
    )

    points = []
    for index, scale in enumerate(order):
        ratio = _ratio(values.get(scale, 0), maximum)
        points.append(_vertex(index, len(order), centre, HEX_RADIUS * ratio))
    shape = " ".join(f"{_r(x)},{_r(y)}" for x, y in points)

    dots = "".join(
        f'<circle cx="{_r(x)}" cy="{_r(y)}" r="3.5" fill="{brand}"/>' for x, y in points
    )

    labels = []
    for index, scale in enumerate(order):
        x, y = _vertex(index, len(order), centre, HEX_LABEL_RADIUS)
        value = values.get(scale, 0)
        labels.append(
            f'<text x="{_r(x)}" y="{_r(y)}" text-anchor="middle" '
            f'dominant-baseline="middle" font-size="15" font-weight="600" '
            f'fill="{INK}">{escape(str(scale))}</text>'
            f'<text x="{_r(x)}" y="{_r(y + 15)}" text-anchor="middle" '
            f'dominant-baseline="middle" font-size="11" fill="{MUTED}">{_number(value)}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {HEX_SIZE} {HEX_SIZE}" '
        f'width="{HEX_SIZE}" height="{HEX_SIZE}" role="img">'
        f"{rings}{spokes}"
        f'<polygon points="{shape}" fill="{brand}" fill-opacity="0.18" '
        f'stroke="{brand}" stroke-width="2" stroke-linejoin="round"/>'
        f"{dots}{''.join(labels)}"
        "</svg>"
    )


def bars(
    rows: list[tuple[str, float, str]],
    minimum: float,
    maximum: float,
    brand: str = DEFAULT_BRAND,
) -> str:
    """Horizontal bars — `(label, value, band)` per row.

    Used for the five personality domains and, when the module is administered,
    the five GET2 subscales. The band name is printed at the end of each bar
    rather than encoded in the bar's colour: "high" is information, and a reader
    should not have to decode a palette to get it.

    `minimum` is not assumed to be zero. A 10-item IPIP domain scores 10..50, so
    a zero-based bar would show a floor of 20% that no student can ever be below
    and make every profile look uniformly middling.
    """
    brand = _colour(brand)
    height = len(rows) * (BAR_HEIGHT + BAR_GAP)
    width = BAR_LABEL_WIDTH + BAR_WIDTH + 90

    parts = []
    for index, (label, value, band) in enumerate(rows):
        y = index * (BAR_HEIGHT + BAR_GAP)
        filled = BAR_WIDTH * _ratio(value - minimum, maximum - minimum)
        parts.append(
            f'<text x="0" y="{_r(y + BAR_HEIGHT / 2)}" dominant-baseline="middle" '
            f'font-size="13" fill="{INK}">{escape(str(label))}</text>'
            f'<rect x="{BAR_LABEL_WIDTH}" y="{y}" width="{BAR_WIDTH}" height="{BAR_HEIGHT}" '
            f'rx="4" fill="{GRID}" fill-opacity="0.55"/>'
            f'<rect x="{BAR_LABEL_WIDTH}" y="{y}" width="{_r(filled)}" height="{BAR_HEIGHT}" '
            f'rx="4" fill="{brand}"/>'
            f'<text x="{BAR_LABEL_WIDTH + BAR_WIDTH + 10}" y="{_r(y + BAR_HEIGHT / 2)}" '
            f'dominant-baseline="middle" font-size="12" fill="{MUTED}">'
            f"{escape(str(band))}</text>"
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img">{"".join(parts)}</svg>'
    )


def _ring(order: tuple[str, ...], centre: float, radius: float) -> str:
    return " ".join(
        f"{_r(x)},{_r(y)}"
        for x, y in (_vertex(i, len(order), centre, radius) for i in range(len(order)))
    )


def _vertex(index: int, count: int, centre: float, radius: float) -> tuple[float, float]:
    """One vertex, clockwise from the top.

    Duplicated deliberately from `web/lib/review.ts`'s `hexagonPoints`: the
    review screen must show the psychologist the same shape the student's PDF
    will, and the two are in different languages with no shared runtime. The
    test asserts the geometry on both sides rather than trusting the comment.
    """
    import math

    angle = (math.tau / count) * index - math.pi / 2
    return centre + math.cos(angle) * radius, centre + math.sin(angle) * radius


def _ratio(value: float, span: float) -> float:
    """Clamped 0..1. A span of zero is a flat chart, never a division error."""
    if span <= 0:
        return 0.0
    return max(0.0, min(float(value) / float(span), 1.0))


def _colour(value: str | None) -> str:
    """A brand hex, or the default.

    Validated because this string goes straight into an SVG attribute. It comes
    from `organisations.brand_hex`, which a counsellor types into the settings
    form — a `"` in that column would otherwise close the attribute and let the
    rest of the value become markup.
    """
    candidate = (value or "").strip()
    if (
        len(candidate) in (4, 7)
        and candidate.startswith("#")
        and all(c in "0123456789abcdefABCDEF" for c in candidate[1:])
    ):
        return candidate
    return DEFAULT_BRAND


def _r(value: float) -> float:
    """Two decimals. Full float repr would triple the SVG's size for no gain."""
    return round(float(value), 2)


def _number(value: float) -> str:
    """Whole numbers without a trailing `.0` — these are counts, not measures."""
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"
