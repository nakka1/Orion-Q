"""Qualify real timestamp placement, dense points and explicit missing data."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, is_dataclass
import math
import unicodedata

import pytest

from quantum_autopilot.service.terminal_dashboard import TimeChart, time_chart


# Unicode Braille dot numbering is column-major: 1,2,3,7 / 4,5,6,8.
DOTS = ((0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (3, 0), (3, 1))


def braille_points(chart):
    """Decode visible dots independently of the chart's coordinate calculation."""
    points = set()
    for row, line in enumerate(chart.lines):
        for column, character in enumerate(line):
            if character == " ":
                continue
            assert 0x2800 <= ord(character) <= 0x28FF
            pattern = ord(character) - 0x2800
            for bit, (dy, dx) in enumerate(DOTS):
                if pattern & (1 << bit):
                    points.add((row * 4 + dy, column * 2 + dx))
    return points


def assert_geometry(chart, width, height, *, ascii_only=False):
    assert isinstance(chart, TimeChart) and isinstance(chart.lines, tuple)
    assert len(chart.lines) <= max(1, min(200, height))
    assert all(len(line) <= max(1, min(512, width)) for line in chart.lines)
    assert all(unicodedata.category(character) != "Cc" for line in chart.lines for character in line)
    if ascii_only:
        assert all(ord(character) < 128 for line in chart.lines for character in line)
    else:
        points = braille_points(chart)
        assert len(points) == chart.occupied_dots
        assert all(0 <= y < max(1, min(200, height)) * 4 and
                   0 <= x < max(1, min(512, width)) * 2 for y, x in points)
    assert chart.collisions == chart.plotted_count - chart.occupied_dots
    assert chart.plotted_count + chart.missing_count + chart.invalid_count == chart.sample_count


def test_fixed_percentage_domain_places_only_real_samples_at_exact_subcells():
    chart = time_chart([0., .5, 1.], 3, 2, times=[10., 40., 70.], end_s=70., bounds=(0., 1.))
    assert chart.mode == "time" and (chart.start_s, chart.end_s) == (10., 70.)
    assert (chart.low, chart.high) == (0., 1.)
    assert (chart.first_sample_s, chart.last_sample_s) == (10., 70.)
    assert (chart.sample_count, chart.plotted_count, chart.occupied_dots, chart.collisions) == (3, 3, 3, 0)
    assert braille_points(chart) == {(7, 0), (4, 2), (0, 5)}
    assert_geometry(chart, 3, 2)


def test_irregular_timestamps_preserve_time_spacing_instead_of_uniform_sample_order():
    chart = time_chart([0., .5, 1.], 31, 2, times=[0., 1., 60.], bounds=(0., 1.))
    assert {x for _y, x in braille_points(chart)} == {0, 1, 61}
    assert chart.mode == "time" and (chart.start_s, chart.end_s) == (0., 60.)
    assert chart.sample_count == 3


def test_time_window_includes_endpoints_and_excludes_old_and_future_records():
    chart = time_chart([100., 0., .5, 1., 200.], 31, 2,
                       times=[9., 10., 40., 70., 71.], end_s=70., bounds=(0., 1.))
    assert (chart.sample_count, chart.excluded_count, chart.invalid_count) == (3, 2, 0)
    assert (chart.first_sample_s, chart.last_sample_s) == (10., 70.)
    assert braille_points(chart) == {(7, 0), (4, 30), (0, 61)}


def test_backend_anchor_does_not_hold_a_sample_until_now_or_invent_a_latest_point():
    chart = time_chart([.5], 31, 2, times=[30.], end_s=60., bounds=(0., 1.))
    assert chart.end_s == 60. and chart.last_sample_s == 30.
    assert braille_points(chart) == {(4, 30)}
    assert chart.plotted_count == chart.occupied_dots == 1


def test_missing_records_leave_no_interpolated_line_or_false_zero_dot():
    chart = time_chart([0., None, None, 1.], 31, 2,
                       times=[0., 20., 40., 60.], bounds=(0., 1.))
    assert chart.sample_count == 4 and chart.missing_count == 2
    assert chart.plotted_count == 2 and chart.invalid_count == 0
    assert braille_points(chart) == {(7, 0), (0, 61)}


def test_real_zero_and_one_are_plotted_but_out_of_domain_values_are_not_clamped():
    values = [0., 1., None, 1.2, -.1, float("nan"), float("inf"), True, "0.2", 10**1000]
    chart = time_chart(values, 20, 2, times=list(range(len(values))), bounds=(0., 1.))
    assert (chart.sample_count, chart.missing_count, chart.invalid_count, chart.plotted_count) == (10, 6, 2, 2)
    assert (chart.low, chart.high) == (0., 1.)
    assert_geometry(chart, 20, 2)


def test_same_subcell_collisions_are_counted_without_losing_received_sample_count():
    chart = time_chart([.5, .5, .5, .5], 2, 2,
                       times=[10., 10., 10., 10.], bounds=(0., 1.))
    assert chart.mode == "time" and chart.sample_count == chart.plotted_count == 4
    assert chart.occupied_dots == 1 and chart.collisions == 3
    assert braille_points(chart) == {(4, 3)}


def test_distinct_subcell_rows_share_a_character_without_becoming_one_sample():
    chart = time_chart([1., 2 / 3, 1 / 3, 0.], 1, 1,
                       times=[60., 60., 60., 60.], bounds=(0., 1.))
    assert chart.sample_count == chart.plotted_count == chart.occupied_dots == 4
    assert chart.collisions == 0
    assert braille_points(chart) == {(0, 1), (1, 1), (2, 1), (3, 1)}


@pytest.mark.parametrize("times", [None, [], [1.], [2., 1.], [True, 2.], [1., None],
                                   [1., "2"], [1., float("nan")], [1., float("inf")], [1., 10**1000]])
def test_absent_or_ambiguous_timestamps_fall_back_to_explicit_right_aligned_sample_order(times):
    chart = time_chart([0., 1.], 3, 2, times=times, bounds=(0., 1.))
    assert chart.mode == "samples" and chart.start_s is None and chart.end_s is None
    assert chart.sample_count == chart.plotted_count == 2
    assert chart.excluded_count == 0
    assert braille_points(chart) == {(7, 4), (0, 5)}


@pytest.mark.parametrize("ascii_only,capacity", [(False, 6), (True, 3)])
def test_sample_order_keeps_only_actual_latest_samples_and_reports_exclusion(ascii_only, capacity):
    chart = time_chart(list(range(10)), 3, 2, ascii_only=ascii_only)
    assert chart.mode == "samples"
    assert chart.sample_count == chart.plotted_count == capacity
    assert chart.excluded_count == 10 - capacity
    assert chart.low == 10 - capacity and chart.high == 9.
    assert_geometry(chart, 3, 2, ascii_only=ascii_only)


@pytest.mark.parametrize("ascii_only", [False, True])
def test_ascii_fallback_uses_same_time_window_and_counts_with_less_raster_resolution(ascii_only):
    chart = time_chart([0., None, 1.], 7, 3, times=[0., 30., 60.],
                       bounds=(0., 1.), ascii_only=ascii_only)
    assert chart.mode == "time" and chart.start_s == 0. and chart.end_s == 60.
    assert chart.sample_count == 3 and chart.plotted_count == 2 and chart.missing_count == 1
    assert chart.occupied_dots == 2 and chart.collisions == 0
    assert_geometry(chart, 7, 3, ascii_only=ascii_only)
    if ascii_only:
        assert chart.lines[0][-1] != " " and chart.lines[-1][0] != " "
        assert sum(character != " " for line in chart.lines for character in line) == 2


def test_rate_axis_has_published_nonnegative_maximum_and_all_zero_is_still_measured():
    positive = time_chart([0., 120., None, -2., 30.], 20, 3, bounds=(0., None))
    assert (positive.low, positive.high) == (0., 120.)
    assert positive.invalid_count == 1 and positive.missing_count == 1 and positive.plotted_count == 3
    zero = time_chart([0., 0., None], 20, 3, bounds=(0., None))
    assert (zero.low, zero.high) == (0., 1.)
    assert zero.plotted_count == 2 and zero.missing_count == 1 and zero.invalid_count == 0
    assert {y for y, _x in braille_points(zero)} == {11}


def test_signed_observable_autoscale_preserves_negative_phase_measurements():
    chart = time_chart([-.4, None, .2], 20, 3, bounds=None)
    assert (chart.low, chart.high) == (-.4, .2)
    assert chart.plotted_count == 2 and chart.invalid_count == 0 and chart.missing_count == 1
    assert {y for y, _x in braille_points(chart)} == {0, 11}


@pytest.mark.parametrize("values", [[-1e308, 0., 1e308], [0., 5e-324, 1e-323], [1e308] * 3])
def test_extreme_finite_autoscale_does_not_overflow_or_drop_real_measurements(values):
    chart = time_chart(values, 10, 3, times=[0., 30., 60.])
    assert chart.plotted_count == 3 and chart.missing_count == chart.invalid_count == 0
    assert math.isfinite(chart.low) and math.isfinite(chart.high)
    assert chart.low == min(values) and chart.high >= max(values)
    assert_geometry(chart, 10, 3)


def test_large_finite_time_range_uses_published_positions_without_overflow():
    chart = time_chart([0., 1.], 3, 2, times=[0., 1e308], end_s=1e308,
                       window_s=1e308, bounds=(0., 1.))
    assert chart.mode == "time" and (chart.start_s, chart.end_s) == (0., 1e308)
    assert braille_points(chart) == {(7, 0), (0, 5)}


def test_latest_finite_timestamp_stays_at_age_zero_when_window_subtraction_loses_precision():
    for explicit_end in (None, 1e308):
        chart = time_chart([1.], 3, 2, times=[1e308], end_s=explicit_end, bounds=(0., 1.))
        assert chart.mode == "time" and chart.end_s == 1e308
        assert chart.first_sample_s == chart.last_sample_s == 1e308
        assert chart.sample_count == chart.plotted_count == chart.occupied_dots == 1
        assert chart.excluded_count == chart.missing_count == chart.invalid_count == chart.collisions == 0
        assert braille_points(chart) == {(0, 5)}


def test_empty_all_missing_and_fully_excluded_data_never_create_dots():
    empty = time_chart([], 10, 3)
    missing = time_chart([None, False, "unavailable"], 10, 3, times=[0., 30., 60.])
    old = time_chart([0., 1.], 10, 3, times=[0., 1.], end_s=120.)
    assert empty.sample_count == empty.plotted_count == empty.missing_count == 0
    assert missing.sample_count == missing.missing_count == 3 and missing.plotted_count == 0
    assert old.sample_count == old.plotted_count == 0 and old.excluded_count == 2
    assert all(not braille_points(chart) for chart in (empty, missing, old))
    assert all(chart.low is None and chart.high is None for chart in (empty, missing, old))


def test_window_classification_uses_timestamp_before_counting_missing_or_invalid_value():
    chart = time_chart([None, -1., 0., None, 2.], 10, 3,
                       times=[0., 1., 60., 90., 121.], end_s=120., bounds=(0., 1.))
    assert (chart.excluded_count, chart.sample_count, chart.missing_count, chart.invalid_count) == (3, 2, 1, 0)
    assert chart.plotted_count == 1


def test_time_mode_does_not_silently_cap_or_resample_many_real_publications():
    count = 10000
    values, times = [0.] * count, [60.] * count
    chart = time_chart(values, 1, 1, times=times, bounds=(0., 1.))
    assert chart.sample_count == chart.plotted_count == count
    assert chart.excluded_count == 0 and chart.occupied_dots == 1 and chart.collisions == count - 1
    assert braille_points(chart) == {(3, 1)}


@pytest.mark.parametrize("bad", [0., -1., False, True, None, "60", float("nan"), float("inf"), 10**1000])
def test_invalid_window_is_rejected_instead_of_publishing_a_false_time_axis(bad):
    with pytest.raises(ValueError):
        time_chart([0.], 10, 3, times=[0.], window_s=bad)


@pytest.mark.parametrize("bad", [False, True, "60", float("nan"), float("inf"), 10**1000])
def test_invalid_explicit_end_is_rejected_instead_of_inventing_a_clock(bad):
    with pytest.raises(ValueError):
        time_chart([0.], 10, 3, times=[0.], end_s=bad)


@pytest.mark.parametrize("width,height", [(0, 0), (1, 1), (2, 2), (31, 3), (240, 12), (999, 999)])
@pytest.mark.parametrize("ascii_only", [False, True])
def test_raster_dimensions_are_clamped_and_every_dot_stays_in_display_cells(width, height, ascii_only):
    chart = time_chart([0., .25, None, .75, 1.], width, height,
                       times=[0., 15., 30., 45., 60.], bounds=(0., 1.), ascii_only=ascii_only)
    assert chart.sample_count == 5 and chart.plotted_count == 4 and chart.missing_count == 1
    assert_geometry(chart, width, height, ascii_only=ascii_only)


def test_chart_is_frozen_deterministic_and_does_not_mutate_authorized_input():
    values, times = [0., None, .5, 1.], [0., 20., 40., 60.]
    original = deepcopy((values, times))
    chart = time_chart(values, 20, 3, times=times, bounds=(0., 1.))
    assert is_dataclass(chart)
    assert time_chart(values, 20, 3, times=times, bounds=(0., 1.)) == chart
    assert (values, times) == original
    with pytest.raises(FrozenInstanceError):
        chart.end_s = 1.


@pytest.mark.parametrize("timestamps", [True, False])
def test_dashboard_percentage_axes_and_time_labels_do_not_infer_seconds_for_sample_order(timestamps):
    from quantum_autopilot.service.terminal_dashboard import render_dashboard
    from tests.test_terminal_dashboard import admitted_state, assert_frame_geometry, snapshot

    raw = snapshot()
    raw["live"]["network_time_s"] = 60.
    raw["live"]["links"][0]["history"] = {
        "t_s": [0., 30., 60.] if timestamps else [],
        "fidelity": [0., None, 1.], "qber": [0., .5, 1.], "skr_bps": [0., 60., 120.],
    }
    frame = render_dashboard(admitted_state("links", "A-B", raw), columns=240, rows=60, now=105.)
    assert_frame_geometry(frame, 240, 60)
    text = frame.text()
    assert "FIDELITY" in text and "QBER" in text and "SECRET KEY RATE" in text
    assert text.count("100%") >= 2 and text.count("0%") >= 2
    if timestamps:
        assert "60s" in text and "0s" in text and "t 0..60s" in text
        assert "sample order" not in text
    else:
        assert "sample order" in text and "older samples" in text and "latest" in text
        assert "60s" not in text and "t 0..60s" not in text
    chart_text = "".join(value for _row, _column, value, role in frame.spans if role == "chart")
    assert any(0x2801 <= ord(character) <= 0x28FF for character in chart_text)
    assert "●" not in chart_text


def test_protocol_time_axis_and_signed_phase_remain_in_the_published_protocol_clock_domain():
    from quantum_autopilot.service.terminal_dashboard import render_dashboard
    from tests.test_terminal_dashboard import admitted_state, assert_frame_geometry, snapshot

    raw = snapshot()
    raw["live"]["network_time_s"] = 1000.
    raw["live"]["links"] = []
    raw["live"]["technologies"]["protocol_series"] = [{
        "session_id": "fixture-phase", "node_ids": ["A"], "technology": "tf_qkd",
        "metric": "phase_reference_residual_rad", "unit": "rad", "bounds": [None, None],
        "time_domain": "protocol_clock_s", "origin": "replay", "source_id": "fixture-protocol-source",
        "samples": [{"at_s": 10., "value": -.4}, {"at_s": 40., "value": None},
                    {"at_s": 70., "value": .2}],
    }]
    frame = render_dashboard(admitted_state("nodes", "A", raw), columns=240, rows=60, now=105.)
    assert_frame_geometry(frame, 240, 60)
    text = frame.text()
    assert "phase_reference_residual_rad" in text and "fixture-protocol-source" in text
    assert "t 10..70s" in text and "-0.4" in text and "0.2" in text
    assert "invalid" not in text and "100%" not in text
    assert any(any(0x2801 <= ord(character) <= 0x28FF for character in value)
               for _row, _column, value, role in frame.spans if role == "chart")
