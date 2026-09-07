"""Hand-written SVG charts (plan §14, CLAUDE.md · charts).

Geometry is asserted arithmetically rather than by rendering and looking. Two
things worth testing here and nowhere else:

* **The hexagon matches `web/lib/review.ts`.** The psychologist's screen and the
  student's PDF must show the same shape, and they are written in two languages
  with no shared runtime. Only a test can hold them together.
* **Scaling is to the instrument's ceiling.** Scaling to the observed maximum
  would make a flat profile look as strong as a peaked one — the single most
  misleading thing a chart in this report could do.
"""

from __future__ import annotations

import math
import re

import pytest

from app.report import charts
from app.scoring.interests import RIASEC

# Coordinates are rounded to two decimals in the SVG (a full float repr would
# triple its size), so a radius recomputed with hypot lands a hundredth out.
# The tolerance is about the parsing, not about the geometry.
TOLERANCE = 0.02


def points(svg: str, index: int = -1) -> list[tuple[float, float]]:
    """Parse the Nth `points="..."` polygon out of an SVG. -1 is the student's."""
    matches = re.findall(r'points="([^"]+)"', svg)
    pairs = matches[index].split()
    return [(float(x), float(y)) for x, y in (pair.split(",") for pair in pairs)]


# ── the hexagon ──────────────────────────────────────────────────────────────


def test_a_full_score_reaches_the_outer_ring():
    """A scale at the instrument's ceiling touches the grid's edge."""
    svg = charts.hexagon(dict.fromkeys(RIASEC, 10), RIASEC, 10)
    centre = charts.HEX_SIZE / 2

    for x, y in points(svg):
        radius = math.hypot(x - centre, y - centre)
        assert radius == pytest.approx(charts.HEX_RADIUS, abs=TOLERANCE)


def test_a_flat_profile_looks_flat_rather_than_being_rescaled():
    """The rule that makes the chart honest.

    A student who checked two boxes per scale has a genuinely low, flat profile.
    Scaling to their own maximum would draw that identically to a student who
    checked ten — every profile would look equally strong and the chart would
    carry no information at all.
    """
    low = charts.hexagon(dict.fromkeys(RIASEC, 2), RIASEC, 10)
    high = charts.hexagon(dict.fromkeys(RIASEC, 10), RIASEC, 10)
    centre = charts.HEX_SIZE / 2

    low_radius = math.hypot(*(c - centre for c in points(low)[0]))
    high_radius = math.hypot(*(c - centre for c in points(high)[0]))

    assert low_radius < high_radius
    assert low_radius == pytest.approx(charts.HEX_RADIUS * 0.2, abs=TOLERANCE)


def test_the_first_vertex_is_at_the_top_and_the_order_runs_clockwise():
    """Holland's hexagon is R-I-A-S-E-C clockwise, and the adjacency is the
    meaningful part — neighbouring interests are related, opposite ones are not.
    A different vertex order would draw a different claim."""
    svg = charts.hexagon(dict.fromkeys(RIASEC, 10), RIASEC, 10)
    centre = charts.HEX_SIZE / 2
    vertices = points(svg)

    x0, y0 = vertices[0]
    assert x0 == round(centre, 2)
    assert y0 < centre  # straight up

    # Second vertex is clockwise from the first: right of centre, above it.
    x1, y1 = vertices[1]
    assert x1 > centre
    assert y1 < centre


def test_the_hexagon_matches_the_review_screen_implementation():
    """`web/lib/review.ts`'s `hexagonPoints` must produce the same geometry.

    Reimplemented here from the TypeScript rather than imported — that is the
    point. If either side changes its angle convention or its rounding, this
    fails and someone has to decide which is right, instead of a psychologist
    and a student seeing two different shapes for one profile.
    """
    values = {"R": 2, "I": 8, "A": 3, "S": 7, "E": 4, "C": 6}
    radius, centre = charts.HEX_RADIUS, charts.HEX_SIZE / 2

    expected = []
    for index, scale in enumerate(RIASEC):
        angle = ((math.pi * 2) / 6) * index - math.pi / 2
        scaled = max(0.0, min(values[scale] / 10, 1.0))
        expected.append(
            (
                round((centre + math.cos(angle) * radius * scaled) * 100) / 100,
                round((centre + math.sin(angle) * radius * scaled) * 100) / 100,
            )
        )

    assert points(charts.hexagon(values, RIASEC, 10)) == expected


def test_every_vertex_is_labelled_with_its_letter_and_value():
    """Colour is never the only signal — this gets photocopied in greyscale."""
    svg = charts.hexagon({"R": 2, "I": 8, "A": 3, "S": 7, "E": 4, "C": 6}, RIASEC, 10)
    for letter in RIASEC:
        assert f">{letter}</text>" in svg
    assert ">8</text>" in svg


def test_a_zero_ceiling_draws_a_collapsed_shape_rather_than_dividing_by_zero():
    svg = charts.hexagon(dict.fromkeys(RIASEC, 5), RIASEC, 0)
    centre = round(charts.HEX_SIZE / 2, 2)
    assert all(point == (centre, centre) for point in points(svg))


# ── bars ─────────────────────────────────────────────────────────────────────


def test_personality_bars_scale_from_the_domain_floor_not_from_zero():
    """A 10-item IPIP domain scores 10..50, never 0.

    Zero-based bars would show a floor of 20% that no student can be below,
    making every profile look uniformly middling and compressing the real
    variation into the top four fifths of the bar.
    """
    svg = charts.bars([("Openness", 10, "very low")], 10, 50)
    filled = re.findall(r'<rect[^>]*width="([\d.]+)"', svg)
    # First rect is the track, second is the fill.
    assert float(filled[1]) == 0.0

    svg = charts.bars([("Openness", 50, "very high")], 10, 50)
    filled = re.findall(r'<rect[^>]*width="([\d.]+)"', svg)
    assert float(filled[1]) == float(charts.BAR_WIDTH)


def test_a_band_name_is_printed_rather_than_encoded_in_colour():
    svg = charts.bars([("Conscientiousness", 33, "average")], 10, 50)
    assert ">average</text>" in svg
    assert ">Conscientiousness</text>" in svg


def test_a_value_outside_the_range_is_clamped_rather_than_overflowing():
    """A bar wider than its track would draw outside the page box."""
    svg = charts.bars([("Openness", 90, "very high")], 10, 50)
    widths = [float(w) for w in re.findall(r'<rect[^>]*width="([\d.]+)"', svg)]
    assert max(widths) == float(charts.BAR_WIDTH)


# ── the brand colour reaches an SVG attribute ────────────────────────────────


def test_a_valid_brand_colour_is_used():
    assert '#7A3E9D' in charts.hexagon({"R": 1}, RIASEC, 10, "#7A3E9D")
    assert '#fff' in charts.hexagon({"R": 1}, RIASEC, 10, "#fff")


def test_a_malformed_brand_colour_falls_back_instead_of_becoming_markup():
    """`organisations.brand_hex` is typed into the settings form by a counsellor
    and interpolated into an SVG attribute. A quote in it would close the
    attribute and let the rest become markup."""
    for hostile in (
        '#fff" onload="alert(1)',
        "red; }</style><script>alert(1)</script>",
        "",
        None,
        "#12345",
        "javascript:alert(1)",
    ):
        svg = charts.hexagon({"R": 1}, RIASEC, 10, hostile)
        assert charts.DEFAULT_BRAND in svg
        assert "onload" not in svg
        assert "<script" not in svg


def test_a_scale_label_is_escaped():
    """Bar labels come from `personality.labels`, which is stored jsonb."""
    svg = charts.bars([("<script>alert(1)</script>", 30, "average")], 10, 50)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
