"""Derived information uses reported measurements exclusively, never truth.

Bearing drift is a description of the measured bearings. The motion estimate
is a separate constant-course grid fit to those bearings, their timestamps
and provenance, and the own-ship positions recorded with them. Classification
multiplies published priors by the spectral feature of each evidence window.
A frequency-change indication compares successive measured lines after removing
the own-ship Doppler recorded as course and speed at each look. Aspect is not
an input.
"""
import math

from . import spectra

DOMAINS = ("surface", "submerged", "biologic")
PRIORS = {
    "supervisor": {"surface": 0.60, "submerged": 0.30, "biologic": 0.10},
    "operator": {"surface": 0.35, "submerged": 0.50, "biologic": 0.15},
}
VISUAL_LIKELIHOOD = {"surface": 0.995, "submerged": 0.003, "biologic": 0.002}
STALE_MINUTES = 20

RANGE_GRID_NM = (1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24)
COURSE_STEP_DEGREES = 15
SPEED_GRID_KN = (0, 3, 6, 9, 12, 15, 18)
ACOUSTIC_SOURCES = ("passive", "focus", "active")
ACOUSTIC_BIAS_DEGREES = 2
ACOUSTIC_BIAS_STEP_DEGREES = 0.5
ACOUSTIC_ERROR_DEGREES = 5
MAST_ERROR_DEGREES = 3
ACTIVE_RANGE_LOW_FACTOR = 0.88
ACTIVE_RANGE_HIGH_FACTOR = 1.12
BASELINE_COURSE_CHANGE_DEGREES = 30
BASELINE_SPAN_MINUTES = 20
BASELINE_DISTINCT_TIMES = 3
NARROW_RANGE_RATIO = 1.5
NARROW_SPEED_SPAN_KN = 3
SUPERVISOR_SPEED_PRIOR_KN = 8
SOUND_SPEED_MPS = 1500.0
OPERATOR_SIGMA = 2.0
SUPERVISOR_SIGMA = 3.0
# Larger than a reversal at the published speed bounds, so a bigger fractional
# shift is an emitted-frequency change rather than Doppler.
DOPPLER_FRACTION_LIMIT = 0.03
LINE_ASSOCIATION_FRACTION = 0.75
_RANGE_INDEX = {value: index for index, value in enumerate(RANGE_GRID_NM)}
_SPEED_INDEX = {value: index for index, value in enumerate(SPEED_GRID_KN)}


def motion_model():
    """Published fit assumptions. Hidden contact motion is not an input."""
    return {
        "modeled": True,
        "hidden_motion_used": False,
        "bearing_drift_used": False,
        "confirmed_target_maneuver": False,
        "motion": "constant course and speed",
        "range_grid_nm": list(RANGE_GRID_NM),
        "course_step_degrees": COURSE_STEP_DEGREES,
        "speed_grid_knots": list(SPEED_GRID_KN),
        "acoustic_sources": list(ACOUSTIC_SOURCES),
        "acoustic_shared_bias_degrees": ACOUSTIC_BIAS_DEGREES,
        "acoustic_independent_error_degrees": ACOUSTIC_ERROR_DEGREES,
        "mast_independent_error_degrees": MAST_ERROR_DEGREES,
        "active_range_factors": {
            "low": ACTIVE_RANGE_LOW_FACTOR,
            "high": ACTIVE_RANGE_HIGH_FACTOR,
        },
        "active_range_rule": (
            "A hypothesized range is accepted when it lies between the reported "
            "range divided by 1.12 and the reported range divided by 0.88."
        ),
        "acoustic_bias_step_degrees": ACOUSTIC_BIAS_STEP_DEGREES,
        "correlation": (
            "Passive, focus and active bearings on one receiver share one bias of at most 2 degrees, "
            "searched in steps of 0.5 degrees, then in steps of 0.1 degrees beside any "
            "step that lands within 1 degree of a gate. Bearings that share an evidence window "
            "contribute one bearing constraint, so a repeated bearing does not tighten "
            "that gate and cannot by itself make the geometry constrained. A track is one "
            "receiver's history; hull, flank and towed detections are not automatically "
            "the same contact."
        ),
        "baseline": (
            "The family is called constrained only when the smallest arc containing "
            "the own-ship track legs is at least 30 degrees, across at least three "
            "measurement times spanning at least 20 minutes, and one narrow group "
            "remains on the grid. Successive smaller turns add."
        ),
        "maneuver": (
            "The estimator does not confirm a contact maneuver. Observed bearing "
            "drift is a separate measurement and is not an input."
        ),
        "range_reference": (
            "Each solution's range is along the latest recorded bearing at the "
            "latest measurement time. Earlier positions follow the constant course and speed."
        ),
        "speed_zero": (
            "A stopped solution has no course. Course does not change a zero-speed track."
        ),
    }


def classification_model():
    """Priors and the rule that ties them to measured spectral features."""
    return {
        "domains": list(DOMAINS),
        "priors": {role: dict(weights) for role, weights in PRIORS.items()},
        "visual_likelihoods": dict(VISUAL_LIKELIHOOD),
        "stale_after_minutes": STALE_MINUTES,
        "feature_likelihoods": {
            name: {domain: weights[index] for index, domain in enumerate(DOMAINS)}
            for name, weights in spectra.FEATURE_LIKELIHOOD.items()
        },
        "update": (
            "Each evidence window contributes one spectral feature, taken from the "
            "latest measured spectrum in that window that resolved a feature. "
            "The feature likelihoods multiply the role prior. A visual observation "
            "replaces that product for both roles. Both roles cite the same reports."
        ),
    }


def frequency_change_model():
    """Published rules for a crew indication that the contact's sound changed."""
    return {
        "modeled": True,
        "hidden_motion_used": False,
        "aspect_modeled": False,
        "confirmed_maneuver": False,
        "sound_speed_m_s": SOUND_SPEED_MPS,
        "operator_sigma": OPERATOR_SIGMA,
        "supervisor_sigma": SUPERVISOR_SIGMA,
        "doppler_fraction_limit": DOPPLER_FRACTION_LIMIT,
        "line_association_fraction": LINE_ASSOCIATION_FRACTION,
        "correlation_window_minutes": spectra.CORRELATION_WINDOW_MINUTES,
        "comparison": (
            "Successive measured lines are matched in frequency order. The "
            "frequency change is reduced by the own-ship closing-speed change "
            f"recorded as course and speed at each look, at {SOUND_SPEED_MPS:g} m/s. "
            "A pair with no recorded speed is not corrected. Inside one evidence "
            "window the shared environmental and receiver calibration bias and the "
            "reused per-line processing sample cancel. Across a window boundary the "
            "full reported uncertainties "
            "apply, so a small shift can stay inside the gate until a later look."
        ),
        "cue": (
            "A residual inside the role's sigma gate is within error. A larger "
            f"residual within {DOPPLER_FRACTION_LIMIT:.0%} of the line is a possible "
            "range-rate change. A larger fraction is an emitted-frequency change, "
            "such as blade rate or machinery, because it exceeds the Doppler bound."
        ),
        "aspect": (
            "Received level does not depend on bow, beam, or stern aspect. "
            "Quality is reported with the matched line and is not itself a zig call."
        ),
    }


def observed_bearing_drift(observations):
    """Compare the newest bearing with the oldest recorded within 30 minutes.

    The 3-degree deadband prevents a small measurement change from becoming a
    confident left/right report. This is a descriptive trend, not a fitted solution
    or a statement of the contact's true motion. Repeated measurements at the same
    instant cannot create a time interval.
    """
    samples = sorted(
        (o for o in observations if "bearing_true" in o and "elapsed_minutes" in o),
        key=lambda o: o["elapsed_minutes"],
    )
    unavailable = {"status": "undetermined", "reason": "At least two observations at different times are required."}
    if len(samples) < 2:
        return unavailable
    latest = samples[-1]
    earlier = [o for o in samples[:-1] if 0 < latest["elapsed_minutes"] - o["elapsed_minutes"] <= 30]
    if not earlier:
        return unavailable
    first = earlier[0]
    span = latest["elapsed_minutes"] - first["elapsed_minutes"]
    change = (latest["bearing_true"] - first["bearing_true"] + 180) % 360 - 180
    if abs(change) == 180:
        return {"status": "undetermined", "reason": "A 180-degree change is ambiguous; inspect contact association and history."}
    trend = "unresolved" if abs(change) <= 3 else "right" if change > 0 else "left"
    return {
        "status": "observed", "direction": trend,
        "change_degrees": round(change, 2),
        "interval_minutes": span,
        "rate_degrees_per_minute": round(change / span, 3),
        "from_time": first.get("time"), "to_time": latest.get("time"),
        "interpretation": "Measured bearing change only; sensor error and own-ship motion contribute. No true target course or speed is inferred.",
    }


def _ang_diff(left, right):
    return (left - right + 180) % 360 - 180


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def _round_degrees(value):
    rounded = round(float(value), 2)
    if rounded == 0:
        return 0.0
    return rounded


def _samples(observations):
    """Copy only the measurement fields the fit is allowed to read."""
    prepared = []
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        elapsed = _number(obs.get("elapsed_minutes"))
        bearing = _number(obs.get("bearing_true"))
        east = _number(obs.get("own_east_nm"))
        north = _number(obs.get("own_north_nm"))
        if None in (elapsed, bearing, east, north):
            continue
        source = obs.get("source") if isinstance(obs.get("source"), str) else "passive"
        reported_range = _number(obs.get("range_estimate_nm"))
        if reported_range is not None and reported_range <= 0:
            reported_range = None
        window = obs.get("evidence_window")
        report_id = obs.get("report_id") if isinstance(obs.get("report_id"), str) else None
        prepared.append({
            "elapsed_minutes": elapsed,
            "bearing_true": bearing % 360,
            "own_east_nm": east,
            "own_north_nm": north,
            "source": source,
            "evidence_window": window if isinstance(window, str) else None,
            "range_estimate_nm": reported_range,
            "report_id": report_id,
            "time": obs.get("time") if isinstance(obs.get("time"), str) else None,
        })
    prepared.sort(key=lambda sample: (
        sample["elapsed_minutes"], sample["source"], sample["bearing_true"],
    ))
    return prepared


def _own_course_change(samples):
    """Smallest arc, in degrees, that contains every own-ship leg of at least 0.2 nm."""
    points = []
    for sample in samples:
        point = (sample["own_east_nm"], sample["own_north_nm"])
        if not points or math.hypot(point[0] - points[-1][0], point[1] - points[-1][1]) >= 0.2:
            points.append(point)
    courses = []
    for start, end in zip(points, points[1:]):
        east = end[0] - start[0]
        north = end[1] - start[1]
        if math.hypot(east, north) < 0.2:
            continue
        courses.append(math.degrees(math.atan2(east, north)) % 360)
    if not courses:
        return 0.0
    return _course_span(courses)


def _course_span(courses):
    ordered = sorted(set(courses))
    if len(ordered) <= 1:
        return 0
    gaps = [ordered[index + 1] - ordered[index] for index in range(len(ordered) - 1)]
    gaps.append(ordered[0] + 360 - ordered[-1])
    return 360 - max(gaps)


def _bias_grid(step):
    count = int(round(2 * ACOUSTIC_BIAS_DEGREES / step))
    return tuple(
        round(-ACOUSTIC_BIAS_DEGREES + index * step, 2)
        for index in range(count + 1)
    )


_COARSE_BIASES = _bias_grid(ACOUSTIC_BIAS_STEP_DEGREES)
_FINE_STEP_DEGREES = 0.1


def _bearing_samples(samples):
    """One acoustic bearing per evidence window. Other looks stay separate."""
    acoustic = {}
    others = []
    for sample in samples:
        window = sample["evidence_window"]
        if window and sample["source"] in ACOUSTIC_SOURCES:
            acoustic[window] = sample
        else:
            others.append(sample)
    return list(acoustic.values()) + others


def _contact_at(reference, sample, range_nm, course, speed, bias):
    place = reference["bearing_true"]
    if reference["source"] in ACOUSTIC_SOURCES:
        place = (place - bias) % 360
    angle = math.radians(place)
    east = reference["own_east_nm"] + math.sin(angle) * range_nm
    north = reference["own_north_nm"] + math.cos(angle) * range_nm
    hours = (sample["elapsed_minutes"] - reference["elapsed_minutes"]) / 60.0
    if speed != 0 and course is not None:
        heading = math.radians(course)
        east += math.sin(heading) * speed * hours
        north += math.cos(heading) * speed * hours
    dx = east - sample["own_east_nm"]
    dy = north - sample["own_north_nm"]
    span = math.hypot(dx, dy)
    if span < 0.05:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360, span


def _fit_error(samples, range_nm, course, speed, bias, bearing_samples=None, abort_excess=None):
    """Return max bearing error and its excess over the gate, or None if invalid.

    Excess is negative when every bearing gate passes. A reported active range
    is still checked on every look that carried one. ``abort_excess`` stops a
    hopeless bias before the remaining looks are evaluated.
    """
    reference = samples[-1]
    max_error = 0.0
    worst_excess = -math.inf
    if bearing_samples is None:
        bearing_samples = _bearing_samples(samples)
    for sample in bearing_samples:
        predicted = _contact_at(reference, sample, range_nm, course, speed, bias)
        if predicted is None:
            return None
        bearing, _span = predicted
        residual = _ang_diff(sample["bearing_true"], bearing)
        if sample["source"] in ACOUSTIC_SOURCES:
            error = abs(residual - bias)
            gate = ACOUSTIC_ERROR_DEGREES
        else:
            error = abs(residual)
            gate = MAST_ERROR_DEGREES
        excess = error - gate
        if abort_excess is not None and excess > abort_excess:
            return None
        max_error = max(max_error, error)
        worst_excess = max(worst_excess, excess)
    for sample in samples:
        reported = sample["range_estimate_nm"]
        if reported is None:
            continue
        predicted = _contact_at(reference, sample, range_nm, course, speed, bias)
        if predicted is None:
            return None
        _bearing, span = predicted
        low = reported / ACTIVE_RANGE_HIGH_FACTOR
        high = reported / ACTIVE_RANGE_LOW_FACTOR
        if not low - 1e-9 <= span <= high + 1e-9:
            return None
    return max_error, worst_excess


def _best_bias(samples, range_nm, course, speed):
    acoustic = any(sample["source"] in ACOUSTIC_SOURCES for sample in samples)
    if not acoustic:
        fitted = _fit_error(samples, range_nm, course, speed, 0.0)
        if fitted is None or fitted[1] > 1e-9:
            return None
        return fitted[0], 0.0, 0.0
    best = None
    nearest = None
    bearing_samples = _bearing_samples(samples)

    def consider(bias, abort_excess):
        nonlocal best, nearest
        fitted = _fit_error(
            samples, range_nm, course, speed, bias,
            bearing_samples=bearing_samples, abort_excess=abort_excess,
        )
        if fitted is None:
            return
        max_error, excess = fitted
        if excess <= 1e-9:
            candidate = (max_error, abs(bias), bias)
            if best is None or candidate < best:
                best = candidate
        if nearest is None or excess < nearest[0]:
            nearest = (excess, bias)

    for bias in _COARSE_BIASES:
        consider(bias, 1.0)
    if best is not None:
        return best
    # A cell can meet the gate between the 0.5-degree steps. Check the tenth
    # of a degree beside the closest step when that step is near the gate.
    if nearest is None or nearest[0] > 1.0:
        return None
    low = max(-float(ACOUSTIC_BIAS_DEGREES), nearest[1] - ACOUSTIC_BIAS_STEP_DEGREES)
    high = min(float(ACOUSTIC_BIAS_DEGREES), nearest[1] + ACOUSTIC_BIAS_STEP_DEGREES)
    bias = low
    while bias <= high + 1e-9:
        consider(round(bias, 2), 0.0)
        bias += _FINE_STEP_DEGREES
    return best


def _acceptable(samples):
    found = []
    for range_nm in RANGE_GRID_NM:
        stopped = _best_bias(samples, range_nm, None, 0)
        if stopped is not None:
            error, _absolute, bias = stopped
            found.append({
                "range_nm": range_nm,
                "course_true": None,
                "speed_knots": 0,
                "max_residual_degrees": _round_degrees(error),
                "acoustic_bias_degrees": bias,
            })
        for speed in SPEED_GRID_KN:
            if speed == 0:
                continue
            for course in range(0, 360, COURSE_STEP_DEGREES):
                fitted = _best_bias(samples, range_nm, course, speed)
                if fitted is None:
                    continue
                error, _absolute, bias = fitted
                found.append({
                    "range_nm": range_nm,
                    "course_true": course,
                    "speed_knots": speed,
                    "max_residual_degrees": _round_degrees(error),
                    "acoustic_bias_degrees": bias,
                })
    found.sort(key=lambda item: (
        item["range_nm"],
        item["speed_knots"],
        -1 if item["course_true"] is None else item["course_true"],
    ))
    return found


def _adjacent(left, right):
    if abs(_RANGE_INDEX[left["range_nm"]] - _RANGE_INDEX[right["range_nm"]]) > 1:
        return False
    if left["course_true"] is None or right["course_true"] is None:
        return left["course_true"] is None and right["course_true"] is None
    if abs(_SPEED_INDEX[left["speed_knots"]] - _SPEED_INDEX[right["speed_knots"]]) > 1:
        return False
    return abs(_ang_diff(left["course_true"], right["course_true"])) <= COURSE_STEP_DEGREES


def _groups(solutions):
    parent = list(range(len(solutions)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left in range(len(solutions)):
        for right in range(left + 1, len(solutions)):
            if _adjacent(solutions[left], solutions[right]):
                union(left, right)
    clusters = {}
    for index, solution in enumerate(solutions):
        clusters.setdefault(find(index), []).append(solution)
    groups = []
    for members in clusters.values():
        courses = [item["course_true"] for item in members if item["course_true"] is not None]
        representative = min(members, key=lambda item: (
            item["max_residual_degrees"],
            item["speed_knots"],
            item["range_nm"],
            -1 if item["course_true"] is None else item["course_true"],
        ))
        groups.append({
            "solution_count": len(members),
            "range_nm": {
                "minimum": min(item["range_nm"] for item in members),
                "maximum": max(item["range_nm"] for item in members),
            },
            "courses_true": None if not courses else sorted(set(courses)),
            "speed_knots": {
                "minimum": min(item["speed_knots"] for item in members),
                "maximum": max(item["speed_knots"] for item in members),
            },
            "representative": dict(representative),
        })
    groups.sort(key=lambda group: (
        group["speed_knots"]["minimum"],
        group["range_nm"]["minimum"],
        -1 if group["representative"]["course_true"] is None else group["representative"]["course_true"],
    ))
    return groups


def _narrow(group):
    ranges = group["range_nm"]
    if ranges["minimum"] <= 0:
        return False
    if ranges["maximum"] / ranges["minimum"] > NARROW_RANGE_RATIO:
        return False
    speeds = group["speed_knots"]
    if speeds["maximum"] - speeds["minimum"] > NARROW_SPEED_SPAN_KN:
        return False
    courses = group["courses_true"]
    if courses is None:
        return True
    return _course_span(courses) <= COURSE_STEP_DEGREES


def _format_solution(solution):
    if solution["course_true"] is None:
        course = "course unset at zero speed"
    else:
        course = f"course {solution['course_true']:g}"
    return (
        f"range {solution['range_nm']:g} nm, {course}, "
        f"speed {solution['speed_knots']:g} kn"
    )


def _report_ids(samples, elapsed_minutes):
    ordered = []
    stale = []
    seen = set()
    for sample in samples:
        report_id = sample["report_id"]
        if report_id is None or report_id in seen:
            continue
        seen.add(report_id)
        ordered.append(report_id)
        if elapsed_minutes is not None and elapsed_minutes - sample["elapsed_minutes"] >= STALE_MINUTES:
            stale.append(report_id)
    return ordered, stale


def _prefer_operator(groups):
    return min(groups, key=lambda group: (
        group["speed_knots"]["minimum"],
        group["range_nm"]["minimum"],
        group["representative"]["max_residual_degrees"],
    ))


def _prefer_supervisor(groups):
    def key(group):
        speeds = group["speed_knots"]
        midpoint = (speeds["minimum"] + speeds["maximum"]) / 2
        return (
            abs(midpoint - SUPERVISOR_SPEED_PRIOR_KN),
            -group["range_nm"]["maximum"],
            group["representative"]["max_residual_degrees"],
        )
    return min(groups, key=key)


def _motion_crew(status, groups, geometry, report_ids, stale_ids):
    operator_group = None
    supervisor_group = None
    if groups and status not in ("insufficient_observations", "inconsistent_with_constant_motion"):
        operator_group = _prefer_operator(groups)
        supervisor_group = _prefer_supervisor(groups)

    def reading(group):
        if status == "insufficient_observations":
            return (
                "Fewer than two bearings at different times are recorded. "
                "No range, course or speed is estimated."
            )
        if status == "inconsistent_with_constant_motion":
            return "No constant course and speed on the published grid meets the stated error model."
        described = _format_solution(group["representative"])
        if status == "underdetermined":
            return (
                "Own-ship geometry leaves range, course and speed underdetermined "
                f"({geometry['own_ship_course_change_degrees']} degrees of own-ship course change, "
                f"{geometry['distinct_times']} measurement times, {geometry['span_minutes']} minutes). "
                f"This role weights the retained group around {described}."
            )
        if status == "ambiguous":
            return (
                "Own-ship motion provides a baseline, and more than one group still fits the bearings. "
                f"This role weights the retained group around {described}."
            )
        return (
            "Own-ship motion and the bearings leave one narrow group on the grid, "
            f"around {described}. The group is an estimate under the stated error model."
        )

    notes = [
        "Observed bearing drift is a separate measurement and is not an input to this estimate.",
    ]
    if status == "inconsistent_with_constant_motion":
        notes.extend([
            "Measurement error beyond the stated gates is one explanation.",
            "A contact course or speed change is another explanation.",
            "Mis-associated bearings are another explanation.",
            "The estimate does not confirm any one of those explanations.",
        ])
    elif status == "underdetermined":
        notes.append(
            "The acceptable solutions are the estimate. Own-ship motion and the error model have not separated them."
        )
    elif status == "ambiguous":
        notes.append("Each retained group meets the same error model.")
    elif status == "constrained":
        notes.append("The retained ranges, courses and speeds are the estimate.")
    if operator_group is not None and supervisor_group is not None:
        same = operator_group["representative"] == supervisor_group["representative"]
        if not same:
            notes.append(
                f"The operator weights {_format_solution(operator_group['representative'])} and the "
                f"supervisor weights {_format_solution(supervisor_group['representative'])}. "
                "Both remain inside the fit of the same reports."
            )
        elif len(groups) > 1:
            rendered = "; ".join(_format_solution(group["representative"]) for group in groups)
            notes.append(f"Retained groups include {rendered}.")
    if stale_ids:
        notes.append(
            f"Reports {', '.join(stale_ids)} are at least {STALE_MINUTES} minutes old. "
            "They stay in the fitted history and are stale."
        )
    cited = ", ".join(report_ids) if report_ids else "none"
    return {
        "shared_report_ids": list(report_ids),
        "stale_report_ids": list(stale_ids),
        "operator": {
            "reading": reading(operator_group),
            "emphasized_solution": None if operator_group is None else dict(operator_group["representative"]),
            "emphasis_established": status == "constrained",
        },
        "supervisor": {
            "reading": reading(supervisor_group),
            "emphasized_solution": None if supervisor_group is None else dict(supervisor_group["representative"]),
            "emphasis_established": status == "constrained",
        },
        "supporting_reports_text": f"Supporting reports: {cited}.",
        "competing_interpretations": notes,
    }


def _maneuver(status):
    if status == "insufficient_observations":
        reason = "There are not enough bearings at different times to support a maneuver conclusion."
        assessment = "not_confirmed"
    elif status == "inconsistent_with_constant_motion":
        reason = (
            "No constant-motion grid point fits. A contact maneuver is one explanation, "
            "with measurement error and mis-association. None is confirmed."
        )
        assessment = "not_confirmed"
    else:
        reason = (
            "A constant course and speed meets the error model, so the bearing change "
            "is carried as own-ship motion and measurement error."
        )
        assessment = "not_indicated"
    return {"confirmed": False, "assessment": assessment, "reason": reason}


def target_motion_estimate(observations, elapsed_minutes=None):
    """Fit constant-motion solutions to recorded bearings and own-ship positions.

    The returned family is the estimate. A single favored cell inside an
    underdetermined family is a role prior, and the other acceptable cells stay
    in the result. Nothing in ``observations`` except the measurement fields is read.
    """
    samples = _samples(observations)
    distinct = []
    for sample in samples:
        if sample["elapsed_minutes"] not in distinct:
            distinct.append(sample["elapsed_minutes"])
    span = 0 if len(distinct) < 2 else distinct[-1] - distinct[0]
    course_change = _round_degrees(_own_course_change(samples)) if samples else 0.0
    geometry = {
        "own_ship_course_change_degrees": course_change,
        "span_minutes": span,
        "distinct_times": len(distinct),
        "baseline": "insufficient",
    }
    report_ids, stale_ids = _report_ids(samples, elapsed_minutes)
    windows = sorted({sample["evidence_window"] for sample in samples if sample["evidence_window"]})
    sources = sorted({sample["source"] for sample in samples})
    measurements = {
        "count": len(samples),
        "distinct_times": len(distinct),
        "earliest_time": samples[0]["time"] if samples else None,
        "latest_time": samples[-1]["time"] if samples else None,
        "earliest_elapsed_minutes": samples[0]["elapsed_minutes"] if samples else None,
        "latest_elapsed_minutes": samples[-1]["elapsed_minutes"] if samples else None,
        "sources": sources,
        "evidence_windows": windows,
        "report_ids": report_ids,
        "stale_report_ids": stale_ids,
    }
    range_reference = None
    if samples:
        reference = samples[-1]
        range_reference = {
            "elapsed_minutes": reference["elapsed_minutes"],
            "time": reference["time"],
            "bearing_true": reference["bearing_true"],
            "source": reference["source"],
        }
    if len(distinct) < 2:
        status = "insufficient_observations"
        solutions = []
        groups = []
    else:
        solutions = _acceptable(samples)
        groups = _groups(solutions)
        baseline = (
            course_change >= BASELINE_COURSE_CHANGE_DEGREES
            and len(distinct) >= BASELINE_DISTINCT_TIMES
            and span >= BASELINE_SPAN_MINUTES
        )
        geometry["baseline"] = "own_ship_course_change" if baseline else "insufficient"
        if not solutions:
            status = "inconsistent_with_constant_motion"
        elif not baseline:
            status = "underdetermined"
        elif len(groups) == 1 and _narrow(groups[0]):
            status = "constrained"
        else:
            status = "ambiguous"
    return {
        "status": status,
        "assumptions": motion_model(),
        "measurements": measurements,
        "geometry": geometry,
        "range_reference": range_reference,
        "acceptable_solutions": solutions,
        "acceptable_solution_count": len(solutions),
        "solution_groups": groups,
        "target_maneuver": _maneuver(status),
        "crew": _motion_crew(status, groups, geometry, report_ids, stale_ids),
    }


def _family_weights(feature):
    raw = spectra.FEATURE_LIKELIHOOD.get(feature)
    if raw is None:
        return None
    return {domain: raw[index] for index, domain in enumerate(DOMAINS)}


def _normalize(weights):
    total = sum(weights[domain] for domain in DOMAINS)
    raw = {domain: weights[domain] / total for domain in DOMAINS}
    rounded = {domain: round(raw[domain], 3) for domain in DOMAINS}
    drift = round(1.0 - sum(rounded.values()), 3)
    if drift:
        key = max(DOMAINS, key=lambda domain: (raw[domain], -DOMAINS.index(domain)))
        rounded[key] = round(rounded[key] + drift, 3)
    return raw, rounded


def _favors(raw):
    return max(DOMAINS, key=lambda domain: (raw[domain], -DOMAINS.index(domain)))


def _confidence(raw):
    best = max(raw.values())
    if best >= 0.85:
        return "high"
    if best >= 0.65:
        return "moderate"
    return "low"


def _window_observations(observations, window_key):
    if not str(window_key).isdigit():
        return []
    label = f"A{int(window_key):03d}"
    matched = [
        obs for obs in observations
        if isinstance(obs, dict) and obs.get("evidence_window") == label
    ]
    matched.sort(key=lambda obs: obs.get("elapsed_minutes") if isinstance(obs.get("elapsed_minutes"), (int, float)) else 0)
    return matched


def _public_lines(spectrum):
    lines = []
    for line in spectrum.get("lines") or []:
        if not isinstance(line, dict):
            continue
        lines.append({
            "measured_hz": line.get("measured_hz"),
            "uncertainty_hz": line.get("uncertainty_hz"),
            "quality": line.get("quality"),
        })
    broadband = []
    for band in spectrum.get("broadband") or []:
        if not isinstance(band, dict):
            continue
        broadband.append({
            "low_hz": band.get("low_hz"),
            "high_hz": band.get("high_hz"),
            "quality": band.get("quality"),
        })
    relations = []
    for relation in spectrum.get("harmonic_relations") or []:
        if not isinstance(relation, dict):
            continue
        relations.append({
            "fundamental_hz": relation.get("fundamental_hz"),
            "multiples": list(relation.get("multiples") or []),
        })
    return lines, broadband, relations


def _stale_observation(obs, elapsed_minutes):
    if elapsed_minutes is None:
        return False
    elapsed = obs.get("elapsed_minutes")
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool):
        return False
    return elapsed_minutes - elapsed >= STALE_MINUTES


def _role_reading(role):
    likelihoods = role["likelihoods"]
    reports = ", ".join(role["supporting_reports"]) if role["supporting_reports"] else "none"
    text = (
        f"Favors {role['favors']} with {role['confidence']} confidence "
        f"(surface {likelihoods['surface']:.3f}, submerged {likelihoods['submerged']:.3f}, "
        f"biologic {likelihoods['biologic']:.3f}). "
        f"Basis: {role['basis']}. Supporting reports: {reports}."
    )
    if role["stale_reports"]:
        text += f" Stale reports: {', '.join(role['stale_reports'])}."
    return text


def crew_classification(track, elapsed_minutes=None):
    """Domain likelihoods from shared observations and the two role priors.

    ``track`` may carry hidden linkage fields. Only ``evidence``, ``visual`` and
    ``observations`` are read, and a spectrum contributes only its public
    measurement fields.
    """
    evidence = track.get("evidence") if isinstance(track.get("evidence"), dict) else {}
    observations = [obs for obs in (track.get("observations") or []) if isinstance(obs, dict)]
    visual = track.get("visual")
    cited = []
    feature_weights = []
    window_keys = sorted(
        evidence,
        key=lambda key: (0, int(key)) if str(key).isdigit() else (1, str(key)),
    )
    for key in window_keys:
        window_obs = _window_observations(observations, key)
        producers = []
        for obs in window_obs:
            spectrum = obs.get("spectrum") if isinstance(obs.get("spectrum"), dict) else None
            if spectrum is None:
                continue
            derived = spectra.spectral_feature(spectrum)
            if derived:
                producers.append((obs, spectrum, derived))
        if producers:
            _source, spectrum, feature = producers[-1]
            cited_obs = [obs for obs, _spectrum, derived in producers if derived == feature]
        else:
            feature = evidence[key]
            spectrum = None
            cited_obs = window_obs
        weights = _family_weights(feature)
        if weights is None:
            continue
        feature_weights.append(weights)
        report_ids = []
        stale_ids = []
        for obs in cited_obs:
            report_id = obs.get("report_id")
            if not isinstance(report_id, str) or report_id in report_ids:
                continue
            report_ids.append(report_id)
            if _stale_observation(obs, elapsed_minutes):
                stale_ids.append(report_id)
        lines, broadband, relations = _public_lines(spectrum) if spectrum is not None else ([], [], [])
        if report_ids:
            window_stale = len(stale_ids) == len(report_ids)
        else:
            window_stale = bool(cited_obs) and _stale_observation(cited_obs[-1], elapsed_minutes)
        cited.append({
            "evidence_window": f"A{int(key):03d}" if str(key).isdigit() else str(key),
            "feature": feature,
            "family_likelihoods": dict(weights),
            "domains_with_likelihood": sum(1 for value in weights.values() if value > 0),
            "report_ids": report_ids,
            "stale_report_ids": stale_ids,
            "stale": window_stale,
            "measured_lines": lines,
            "broadband": broadband,
            "harmonic_relations": relations,
            "correlation": (
                "One feature is kept for this evidence window. "
                "The listed reports share that update."
            ),
        })
    basis = "signature_families" if feature_weights else "prior_only"
    raw_by_role = {}
    if visual:
        basis = "visual"
        raw_by_role = {role: dict(VISUAL_LIKELIHOOD) for role in PRIORS}
    else:
        for role, prior in PRIORS.items():
            weights = dict(prior)
            for feature in feature_weights:
                weights = {domain: weights[domain] * feature[domain] for domain in DOMAINS}
            raw, _rounded = _normalize(weights)
            raw_by_role[role] = raw
    supporting = []
    stale_reports = []
    if basis == "visual" and isinstance(visual, dict) and isinstance(visual.get("report_id"), str):
        supporting = [visual["report_id"]]
        if _stale_observation(visual, elapsed_minutes):
            stale_reports = [visual["report_id"]]
    elif basis == "signature_families":
        for item in cited:
            for report_id in item["report_ids"]:
                if report_id not in supporting:
                    supporting.append(report_id)
            for report_id in item["stale_report_ids"]:
                if report_id not in stale_reports:
                    stale_reports.append(report_id)
    acoustic_replaced = []
    if basis == "visual":
        visual_id = visual.get("report_id") if isinstance(visual, dict) else None
        for obs in observations:
            if obs.get("source") == "mast":
                continue
            report_id = obs.get("report_id")
            if not isinstance(report_id, str) or report_id == visual_id:
                continue
            if report_id not in acoustic_replaced:
                acoustic_replaced.append(report_id)
    roles = {}
    for role, prior in PRIORS.items():
        raw = raw_by_role[role]
        _raw, rounded = _normalize(raw)
        built = {
            "favors": _favors(raw),
            "confidence": _confidence(raw),
            "likelihoods": rounded,
            "prior": dict(prior),
            "basis": basis,
            "supporting_reports": list(supporting),
            "stale_reports": list(stale_reports),
        }
        built["reading"] = _role_reading(built)
        roles[role] = built
    notes = []
    if basis == "prior_only":
        notes.append(
            "No signature or visual report is cited. Both readings are the stated priors."
        )
    elif basis == "visual":
        notes.append(
            "Both roles use the same visual report. The visual likelihood replaces the "
            "acoustic feature update for both roles, so the acoustic reports are a shared "
            "history rather than a second classification."
        )
    else:
        notes.append("Each cited spectral feature assigns likelihood to every contact domain.")
        if roles["supervisor"]["favors"] != roles["operator"]["favors"]:
            notes.append(
                f"The supervisor favors {roles['supervisor']['favors']} and the operator "
                f"favors {roles['operator']['favors']} because their priors differ. "
                "They cite the same reports."
            )
        else:
            notes.append(
                "The two roles weight the same reports differently because their priors differ. "
                "The readings are one shared evidence set."
            )
        for role in ("supervisor", "operator"):
            raw = raw_by_role[role]
            best = roles[role]["favors"]
            second = max(
                (domain for domain in DOMAINS if domain != best),
                key=lambda domain: raw[domain],
            )
            if raw[second] >= 0.5 * raw[best]:
                notes.append(
                    f"The {role} likelihood for {second} remains close to {best} on these reports."
                )
    if stale_reports:
        notes.append(
            f"Reports {', '.join(stale_reports)} are at least {STALE_MINUTES} minutes old."
        )
    return {
        "supervisor": roles["supervisor"],
        "operator": roles["operator"],
        "cited_evidence": cited,
        "evidence_relationship": {
            "shared_report_ids": list(supporting),
            "stale_report_ids": list(stale_reports),
            "acoustic_reports_replaced_by_visual": acoustic_replaced,
            "independent_corroboration": False,
            "summary": (
                "Both assessments use the same observations; they are not independent corroboration."
            ),
            "competing_interpretations": notes,
        },
    }


def _round_hz(value):
    rounded = round(float(value), 3)
    if rounded == 0:
        return 0.0
    return rounded


def _spectrum_lines(observation):
    spectrum = observation.get("spectrum")
    if not isinstance(spectrum, dict):
        return []
    lines = []
    for line in spectrum.get("lines") or []:
        if not isinstance(line, dict):
            continue
        frequency = _number(line.get("measured_hz"))
        uncertainty = _number(line.get("uncertainty_hz"))
        if frequency is None or frequency <= 0 or uncertainty is None or uncertainty < 0:
            continue
        quality = line.get("quality") if line.get("quality") in ("weak", "moderate", "strong") else None
        lines.append({
            "measured_hz": frequency,
            "uncertainty_hz": uncertainty,
            "quality": quality,
        })
    lines.sort(key=lambda item: item["measured_hz"])
    return lines


def _frequency_samples(observations):
    prepared = []
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        elapsed = _number(obs.get("elapsed_minutes"))
        if elapsed is None:
            continue
        lines = _spectrum_lines(obs)
        if not lines:
            continue
        bearing = _number(obs.get("bearing_true"))
        east = _number(obs.get("own_east_nm"))
        north = _number(obs.get("own_north_nm"))
        course = _number(obs.get("own_course_true"))
        speed = _number(obs.get("own_speed_knots"))
        window = obs.get("evidence_window")
        prepared.append({
            "elapsed_minutes": elapsed,
            "time": obs.get("time") if isinstance(obs.get("time"), str) else None,
            "bearing_true": None if bearing is None else bearing % 360,
            "own_east_nm": east,
            "own_north_nm": north,
            "own_course_true": None if course is None else course % 360,
            "own_speed_knots": speed,
            "evidence_window": window if isinstance(window, str) else None,
            "report_id": obs.get("report_id") if isinstance(obs.get("report_id"), str) else None,
            "lines": lines,
        })
    prepared.sort(key=lambda sample: (sample["elapsed_minutes"], sample["time"] or ""))
    return prepared


def _endpoint_velocity(sample):
    """Own-ship east/north knots recorded at the look. Positions are not a substitute."""
    course = sample.get("own_course_true")
    speed = sample.get("own_speed_knots")
    if course is None or speed is None:
        return None
    radians = math.radians(course)
    return speed * math.sin(radians), speed * math.cos(radians)


def _closing_knots(velocity, bearing):
    if velocity is None or bearing is None:
        return None
    east, north = velocity
    radians = math.radians(bearing)
    return east * math.sin(radians) + north * math.cos(radians)


def _bias_sigma_hz(frequency):
    return frequency * spectra.combined_bias_fraction_bound() / math.sqrt(3.0)


def _motion_sigma_hz(frequency):
    return (
        frequency
        * (spectra.MOTION_UNCERTAINTY_KNOTS * spectra.KNOTS_TO_METERS_PER_SECOND)
        / SOUND_SPEED_MPS
    )


def _component_sigmas(uncertainty, frequency):
    bias = _bias_sigma_hz(frequency)
    motion = _motion_sigma_hz(frequency)
    processing = math.sqrt(max(uncertainty ** 2 - bias ** 2 - motion ** 2, 0.0))
    return processing, bias, motion


def _difference_sigma_hz(left, right, same_window):
    """Sigma of a frequency difference.

    Inside one evidence window the per-line processing sample is reused, so it
    cancels except for a change in its width, and the shared environmental and
    receiver calibration biases cancel except for the difference in line frequency.
    Across a window both are independent.
    """
    processing_left, bias_left, motion_left = _component_sigmas(
        left["uncertainty_hz"], left["measured_hz"]
    )
    processing_right, bias_right, motion_right = _component_sigmas(
        right["uncertainty_hz"], right["measured_hz"]
    )
    if not same_window:
        return math.hypot(
            max(left["uncertainty_hz"], motion_left),
            max(right["uncertainty_hz"], motion_right),
        )
    return math.hypot(
        abs(processing_right - processing_left),
        math.hypot(abs(bias_right - bias_left), math.hypot(motion_left, motion_right)),
    )


def _match_lines(earlier, later):
    earlier = sorted(earlier, key=lambda line: line["measured_hz"])
    later = sorted(later, key=lambda line: line["measured_hz"])

    def within(left, right):
        return abs(left["measured_hz"] - right["measured_hz"]) <= (
            LINE_ASSOCIATION_FRACTION * min(left["measured_hz"], right["measured_hz"])
        )

    if earlier and len(earlier) == len(later) and all(
        within(left, right) for left, right in zip(earlier, later)
    ):
        return list(zip(earlier, later)), 0, 0
    pairs = sorted(
        (
            (abs(left["measured_hz"] - right["measured_hz"]), left_index, right_index)
            for left_index, left in enumerate(earlier)
            for right_index, right in enumerate(later)
        ),
        key=lambda item: item[0],
    )
    used_left, used_right = set(), set()
    matched = []
    for _difference, left_index, right_index in pairs:
        if left_index in used_left or right_index in used_right:
            continue
        if not within(earlier[left_index], later[right_index]):
            continue
        used_left.add(left_index)
        used_right.add(right_index)
        matched.append((earlier[left_index], later[right_index]))
    return matched, len(earlier) - len(used_left), len(later) - len(used_right)


def _frequency_cue(sigma_ratio, fractional):
    if sigma_ratio < OPERATOR_SIGMA:
        return "within_error"
    if fractional > DOPPLER_FRACTION_LIMIT:
        return "emitted_frequency"
    return "radial_rate"


def _compare_spectra(earlier, later, earlier_velocity, later_velocity):
    same_window = (
        earlier["evidence_window"] is not None
        and earlier["evidence_window"] == later["evidence_window"]
    )
    closing_earlier = _closing_knots(earlier_velocity, earlier["bearing_true"])
    closing_later = _closing_knots(later_velocity, later["bearing_true"])
    own_removed = closing_earlier is not None and closing_later is not None
    closing_change = None if not own_removed else closing_later - closing_earlier
    matched, unmatched_earlier, unmatched_later = _match_lines(earlier["lines"], later["lines"])
    lines = []
    for left, right in matched:
        raw = right["measured_hz"] - left["measured_hz"]
        if own_removed:
            own_hz = left["measured_hz"] * (
                closing_change * spectra.KNOTS_TO_METERS_PER_SECOND
            ) / SOUND_SPEED_MPS
        else:
            own_hz = 0.0
        residual = raw - own_hz
        sigma = _difference_sigma_hz(left, right, same_window)
        ratio = abs(residual) / sigma if sigma > 0 else 0.0
        fractional = abs(residual) / left["measured_hz"]
        lines.append({
            "from_hz": _round_hz(left["measured_hz"]),
            "to_hz": _round_hz(right["measured_hz"]),
            "uncertainty_from_hz": _round_hz(left["uncertainty_hz"]),
            "uncertainty_to_hz": _round_hz(right["uncertainty_hz"]),
            "raw_change_hz": _round_hz(raw),
            "own_ship_doppler_hz": _round_hz(own_hz),
            "residual_hz": _round_hz(residual),
            "sigma_hz": _round_hz(sigma),
            "sigma_ratio": _round_hz(ratio),
            "fractional_residual": _round_hz(fractional),
            "cue": _frequency_cue(ratio, fractional),
            "quality_from": left["quality"],
            "quality_to": right["quality"],
            "_ratio": ratio,
        })
    return {
        "from_report_id": earlier["report_id"],
        "to_report_id": later["report_id"],
        "from_time": earlier["time"],
        "to_time": later["time"],
        "from_elapsed_minutes": earlier["elapsed_minutes"],
        "to_elapsed_minutes": later["elapsed_minutes"],
        "same_evidence_window": same_window,
        "own_ship_doppler_removed": own_removed,
        "own_ship_closing_change_knots": None if closing_change is None else _round_hz(closing_change),
        "matched_lines": lines,
        "unmatched_earlier": unmatched_earlier,
        "unmatched_later": unmatched_later,
    }


def _passing(comparison, threshold):
    return [
        line for line in comparison["matched_lines"]
        if line["_ratio"] >= threshold
    ]


def _best_comparison(comparisons, threshold):
    best = None
    for comparison in comparisons:
        passing = _passing(comparison, threshold)
        if not passing:
            continue
        score = (max(line["_ratio"] for line in passing), len(passing))
        if best is None or score > best[0]:
            best = (score, comparison, passing)
    return best


def _change_confidence(passing):
    emitted = any(line["cue"] == "emitted_frequency" for line in passing)
    signs = []
    for line in passing:
        if line["residual_hz"] == 0:
            continue
        signs.append(1 if line["residual_hz"] > 0 else -1)
    agree = len(passing) >= 2 and len(set(signs)) == 1
    if emitted and agree:
        return "high"
    if emitted or agree:
        return "moderate"
    return "low"


def _role_frequency(comparisons, threshold, label):
    best = _best_comparison(comparisons, threshold)
    if best is None:
        return {
            "indicated": False,
            "confidence": "not_indicated",
            "sigma_gate": threshold,
            "supporting_reports": [],
            "cue": None,
            "reading": (
                f"The {label} gate is {threshold:g} sigma. "
                "No matched line exceeds it after own-ship motion is removed."
            ),
        }
    _score, comparison, passing = best
    confidence = _change_confidence(passing)
    strongest = max(passing, key=lambda line: line["_ratio"])
    reports = []
    for report_id in (comparison["from_report_id"], comparison["to_report_id"]):
        if report_id and report_id not in reports:
            reports.append(report_id)
    quality = ""
    if strongest["quality_from"] and strongest["quality_to"] and strongest["quality_from"] != strongest["quality_to"]:
        quality = (
            f" Quality on that line changed from {strongest['quality_from']} "
            f"to {strongest['quality_to']}."
        )
    cue = strongest["cue"]
    return {
        "indicated": True,
        "confidence": confidence,
        "sigma_gate": threshold,
        "supporting_reports": reports,
        "cue": cue,
        "from_hz": strongest["from_hz"],
        "to_hz": strongest["to_hz"],
        "residual_hz": strongest["residual_hz"],
        "sigma_ratio": strongest["sigma_ratio"],
        "reading": (
            f"The {label} {threshold:g}-sigma gate is met from {comparison['from_time']} "
            f"to {comparison['to_time']}: {strongest['from_hz']} Hz to {strongest['to_hz']} Hz, "
            f"residual {strongest['residual_hz']} Hz ({strongest['sigma_ratio']} sigma), "
            f"read as {cue}.{quality} "
            f"Supporting reports: {', '.join(reports) if reports else 'none'}."
        ),
    }


def _strip_ratio(comparison):
    public = dict(comparison)
    public["matched_lines"] = [
        {key: value for key, value in line.items() if key != "_ratio"}
        for line in comparison["matched_lines"]
    ]
    return public


def frequency_change_assessment(observations, elapsed_minutes=None):
    """Crew indication that measured frequencies changed beyond the error model.

    Own-ship Doppler is taken from the recorded track. Hidden contact motion,
    emitted frequency, and aspect are not read. A result can be indicated with
    low confidence on one look and become clearer on a later look.
    """
    samples = _frequency_samples(observations)
    pairs = [(index, index + 1) for index in range(len(samples) - 1)]
    if len(samples) > 2:
        pairs.append((0, len(samples) - 1))
    comparisons = [
        _compare_spectra(
            samples[start], samples[end],
            _endpoint_velocity(samples[start]), _endpoint_velocity(samples[end]),
        )
        for start, end in pairs
    ]
    reviewed = []
    stale = []
    for sample in samples:
        report_id = sample["report_id"]
        if not report_id or report_id in reviewed:
            continue
        reviewed.append(report_id)
        if elapsed_minutes is not None and elapsed_minutes - sample["elapsed_minutes"] >= STALE_MINUTES:
            stale.append(report_id)
    operator = _role_frequency(comparisons, OPERATOR_SIGMA, "operator")
    supervisor = _role_frequency(comparisons, SUPERVISOR_SIGMA, "supervisor")
    notes = [
        "The indication uses measured frequencies and the recorded own-ship track. "
        "It does not confirm a contact maneuver and it does not solve course or speed.",
        "Received level does not depend on aspect, so a bow or beam turn is not a level cue here.",
    ]
    if operator["indicated"] and not supervisor["indicated"]:
        notes.append(
            "The operator's 2-sigma gate is met. The supervisor's 3-sigma gate is not. "
            "Both roles use the same frequency residuals."
        )
    elif operator["indicated"] and supervisor["indicated"]:
        notes.append(
            "Both roles see a frequency change outside their gates on the same residuals."
        )
    elif not operator["indicated"]:
        notes.append(
            "Every matched frequency change lies inside both gates after own-ship motion is removed."
        )
    cues = {role["cue"] for role in (operator, supervisor) if role["cue"]}
    if "emitted_frequency" in cues:
        notes.append(
            "At least one residual exceeds the 3 percent Doppler bound, so the emitted "
            "frequency changed. Blade rate or machinery can do that."
        )
    elif "radial_rate" in cues:
        notes.append(
            "The residual is inside the 3 percent Doppler bound, so it can be a change in range rate."
        )
    if stale:
        notes.append(
            f"Reports {', '.join(stale)} are at least {STALE_MINUTES} minutes old. "
            "They remain in the frequency history and are stale."
        )
    indicated = operator["indicated"] or supervisor["indicated"]
    return {
        "status": "indicated" if indicated else "not_indicated",
        "confirmed_maneuver": False,
        "assumptions": frequency_change_model(),
        "measurements": {
            "spectrum_count": len(samples),
            "report_ids": reviewed,
            "stale_report_ids": stale,
        },
        "comparisons": [_strip_ratio(comparison) for comparison in comparisons],
        "crew": {
            "shared_report_ids": reviewed,
            "stale_report_ids": stale,
            "operator": operator,
            "supervisor": supervisor,
            "competing_interpretations": notes,
        },
    }
