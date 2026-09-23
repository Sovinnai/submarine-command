"""Derived information uses reported measurements exclusively, never truth.

Bearing drift is a description of the measured bearings. The motion estimate
is a separate constant-course grid fit to those bearings, their timestamps
and provenance, and the own-ship positions recorded with them. Classification
multiplies published priors by the spectral feature of each evidence window.
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
        "correlation": (
            "Passive, focus and active bearings share one bias of at most 2 degrees. "
            "Repeated bearings in one evidence window do not tighten that gate and "
            "cannot by themselves make the geometry constrained."
        ),
        "baseline": (
            "The family is called constrained only when own-ship course changes by "
            "at least 30 degrees across at least three measurement times spanning "
            "at least 20 minutes, and one narrow group remains on the grid."
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
            "latest measured spectrum in that window when a spectrum is present. "
            "The feature likelihoods multiply the role prior. A visual observation "
            "replaces that product for both roles. Both roles cite the same reports."
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
    """Largest course change between own-ship track segments of at least 0.2 nm."""
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
    change = 0.0
    for start, end in zip(courses, courses[1:]):
        change = max(change, abs(_ang_diff(end, start)))
    return change


def _course_span(courses):
    ordered = sorted(set(courses))
    if len(ordered) <= 1:
        return 0
    gaps = [ordered[index + 1] - ordered[index] for index in range(len(ordered) - 1)]
    gaps.append(ordered[0] + 360 - ordered[-1])
    return 360 - max(gaps)


def _evaluate(samples, range_nm, course, speed, bias):
    reference = samples[-1]
    place = reference["bearing_true"]
    if reference["source"] in ACOUSTIC_SOURCES:
        place = (place - bias) % 360
    angle = math.radians(place)
    x_ref = reference["own_east_nm"] + math.sin(angle) * range_nm
    y_ref = reference["own_north_nm"] + math.cos(angle) * range_nm
    t_ref = reference["elapsed_minutes"]
    max_error = 0.0
    for sample in samples:
        hours = (sample["elapsed_minutes"] - t_ref) / 60.0
        if speed == 0 or course is None:
            east, north = x_ref, y_ref
        else:
            heading = math.radians(course)
            east = x_ref + math.sin(heading) * speed * hours
            north = y_ref + math.cos(heading) * speed * hours
        dx = east - sample["own_east_nm"]
        dy = north - sample["own_north_nm"]
        if math.hypot(dx, dy) < 0.05:
            return None
        predicted = math.degrees(math.atan2(dx, dy)) % 360
        residual = _ang_diff(sample["bearing_true"], predicted)
        if sample["source"] in ACOUSTIC_SOURCES:
            error = residual - bias
            gate = ACOUSTIC_ERROR_DEGREES
        else:
            error = residual
            gate = MAST_ERROR_DEGREES
        if abs(error) > gate + 1e-9:
            return None
        max_error = max(max_error, abs(error))
        reported = sample["range_estimate_nm"]
        if reported is not None:
            predicted_range = math.hypot(dx, dy)
            low = reported / ACTIVE_RANGE_HIGH_FACTOR
            high = reported / ACTIVE_RANGE_LOW_FACTOR
            if not low - 1e-9 <= predicted_range <= high + 1e-9:
                return None
    return max_error


def _best_bias(samples, range_nm, course, speed):
    acoustic = any(sample["source"] in ACOUSTIC_SOURCES for sample in samples)
    biases = range(-ACOUSTIC_BIAS_DEGREES, ACOUSTIC_BIAS_DEGREES + 1) if acoustic else (0,)
    best = None
    for bias in biases:
        error = _evaluate(samples, range_nm, course, speed, bias)
        if error is None:
            continue
        candidate = (error, abs(bias), bias)
        if best is None or candidate < best:
            best = candidate
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
    course_change = _own_course_change(samples) if samples else 0.0
    geometry = {
        "own_ship_course_change_degrees": _round_degrees(course_change),
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
        latest = window_obs[-1] if window_obs else None
        feature = evidence[key]
        spectrum = latest.get("spectrum") if latest and isinstance(latest.get("spectrum"), dict) else None
        if spectrum is not None:
            derived = spectra.spectral_feature(spectrum)
            if derived:
                feature = derived
        weights = _family_weights(feature)
        if weights is None:
            continue
        feature_weights.append(weights)
        report_ids = []
        stale_ids = []
        for obs in window_obs:
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
            window_stale = latest is not None and _stale_observation(latest, elapsed_minutes)
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
        for obs in observations:
            report_id = obs.get("report_id")
            if isinstance(report_id, str) and report_id not in acoustic_replaced:
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
