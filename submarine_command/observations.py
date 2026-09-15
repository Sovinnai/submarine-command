"""Derived information uses reported measurements exclusively, never truth."""


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
