"""Persistent narrowband spectra and noisy frequency measurements.

Hidden emitters carry fictional tonal families, harmonics and broadband
shapes. Operating mode and speed change what is emitted through the rules
below. Own-ship reception reports measured frequency, quality and uncertainty.
A measurement is evidence. It is not an identity lookup, and the captain's
report does not contain the emitted frequency or the signature family.

Units are Hz and dB re 1 µPa at 1 m, matching submarine_command.acoustics.
All numeric signature values are fictional game parameters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import acoustics
from . import arrays
from .platforms import get_spec


# One acoustic window, shared with the engine's environmental quality draw.
CORRELATION_WINDOW_MINUTES = 20

# Shared fractional frequency bias, uniform over ± this bound, one draw per
# window for every line and every contact. It stands in for a common
# sound-speed error. Each receiver adds a smaller calibration bias.
BIAS_FRACTION_BOUND = 0.0015
ARRAY_BIAS_FRACTION_BOUND = 0.0008


def combined_bias_fraction_bound():
    """Root-sum-square of the environmental and per-receiver fractional bounds."""
    return math.hypot(BIAS_FRACTION_BOUND, ARRAY_BIAS_FRACTION_BOUND)

# Unresolved radial rate used only to widen the reported uncertainty.
MOTION_UNCERTAINTY_KNOTS = 0.4
KNOTS_TO_METERS_PER_SECOND = 1852.0 / 3600.0

NARROWBAND_DT_DB = 4.0
BROADBAND_DT_DB = 8.0

# Fictional processing times inside one five-minute step.
INTEGRATION_SECONDS = {
    "listen": 60.0,
    "active": 60.0,
    "focus_on_contact": 180.0,
    "focus_elsewhere": 20.0,
}

# Fixed analysis bands. Reports name these bands, not an emitter's private
# bandwidth, so a band edge is not a class label.
ANALYSIS_BANDS = (
    (10.0, 200.0),
    (200.0, 1000.0),
    (1000.0, 5000.0),
)

# Likelihood of an observed spectral feature given surface, submerged, biologic.
# Every feature is possible for every domain. No row is an identity lookup.
FEATURE_LIKELIHOOD = {
    "harmonic_set": (0.50, 0.32, 0.18),
    "isolated_tonal": (0.36, 0.38, 0.26),
    "broadband_only": (0.40, 0.25, 0.35),
    "high_band_energy": (0.22, 0.20, 0.58),
    "mixed": (0.40, 0.35, 0.25),
}


@dataclass(frozen=True)
class TonalFamily:
    """One persistent harmonic family.

    Exactly one of fundamental_hz or hz_per_knot is set. hz_per_knot is the
    blade-rate rule: emitted fundamental equals that coefficient times speed
    in knots. Harmonics are integer multiples of the fundamental actually emitted.
    """

    identifier: str
    harmonics: tuple[int, ...]
    fundamental_source_level_db: float
    harmonic_rolloff_db: float
    fundamental_hz: float | None = None
    hz_per_knot: float | None = None
    modes: tuple[str, ...] | None = None
    minimum_speed_knots: float = 0.0
    reference_speed_knots: float | None = None
    speed_level_exponent_db: float = 0.0
    level_follows_mode_noise: bool = True

    def __post_init__(self):
        if (self.fundamental_hz is None) == (self.hz_per_knot is None):
            raise ValueError(f"{self.identifier} needs exactly one frequency rule.")
        if not self.harmonics or any(n < 1 for n in self.harmonics):
            raise ValueError(f"{self.identifier} harmonics must be positive integers.")


@dataclass(frozen=True)
class BroadbandShape:
    """Integrated band level, dB re 1 µPa at 1 m, before mode and speed terms."""

    identifier: str
    low_hz: float
    high_hz: float
    band_level_db: float
    modes: tuple[str, ...] | None = None
    reference_speed_knots: float = 8.0
    speed_level_exponent_db: float = 10.0
    level_follows_mode_noise: bool = True

    def __post_init__(self):
        if self.high_hz <= self.low_hz:
            raise ValueError(f"{self.identifier} band is empty.")


@dataclass(frozen=True)
class SignatureTemplate:
    families: tuple[TonalFamily, ...]
    broadband: tuple[BroadbandShape, ...]


def _tones(*families):
    return tuple(families)


def _wash(*shapes):
    return tuple(shapes)


# Nominal frequencies are deliberately shared across categories. A line near
# 52 Hz can be a fishing vessel or a biologic moan; a line near 60 Hz can be
# a quiet surface auxiliary or a submerged electric motor; a line near 2.2 kHz
# can be fishing gear or a biologic click. Individual offsets move a hull
# slightly off the nominal value without removing the overlap.
SIGNATURES = {
    "kestrel-ssn-v2": SignatureTemplate(
        _tones(
            TonalFamily(
                "blade_rate", (1, 2, 3, 4, 5), 128.0, 3.0,
                hz_per_knot=0.92, minimum_speed_knots=2.0,
                reference_speed_knots=8.0, speed_level_exponent_db=10.0,
            ),
            TonalFamily(
                "ship_service", (1, 2), 124.0, 4.0, fundamental_hz=60.0,
            ),
            TonalFamily(
                "pump", (1,), 118.0, 0.0, fundamental_hz=180.0,
                modes=("standard", "high_power"),
            ),
        ),
        _wash(BroadbandShape("machinery_wash", 20.0, 300.0, 136.0, reference_speed_knots=8.0)),
    ),
    "dart-diesel-aip-v1": SignatureTemplate(
        _tones(
            TonalFamily(
                "blade_rate", (1, 2, 3, 4), 122.0, 3.0,
                hz_per_knot=1.40, minimum_speed_knots=1.0,
                reference_speed_knots=5.0, speed_level_exponent_db=10.0,
            ),
            TonalFamily(
                "electric_motor", (1, 2, 3), 128.0, 5.0, fundamental_hz=60.0,
                modes=("battery", "aip"),
            ),
            TonalFamily(
                "aip_plant", (1, 2), 120.0, 4.0, fundamental_hz=72.0,
                modes=("aip",),
            ),
            TonalFamily(
                "snorkel_diesel", (1, 2, 3), 148.0, 4.0, fundamental_hz=49.0,
                modes=("snorkeling",),
            ),
        ),
        _wash(
            BroadbandShape(
                "submerged_wash", 25.0, 250.0, 132.0,
                modes=("battery", "aip"), reference_speed_knots=5.0,
            ),
            BroadbandShape(
                "snorkel_wash", 20.0, 900.0, 158.0,
                modes=("snorkeling",), reference_speed_knots=6.0,
                speed_level_exponent_db=8.0,
            ),
        ),
    ),
    "alder-merchant-v1": SignatureTemplate(
        _tones(
            TonalFamily(
                "blade_rate", (1, 2, 3, 4, 5), 146.0, 3.0,
                hz_per_knot=1.15, minimum_speed_knots=3.0,
                reference_speed_knots=12.0, speed_level_exponent_db=10.0,
            ),
            TonalFamily(
                "engine_order", (1, 2), 144.0, 5.0, fundamental_hz=47.5,
            ),
        ),
        _wash(BroadbandShape("machinery_wash", 15.0, 350.0, 156.0, reference_speed_knots=12.0)),
    ),
    "warden-surface-combatant-v1": SignatureTemplate(
        _tones(
            TonalFamily(
                "blade_rate", (1, 2, 3, 4, 5, 6), 148.0, 3.0,
                hz_per_knot=1.08, minimum_speed_knots=3.0,
                reference_speed_knots=14.0, speed_level_exponent_db=10.0,
            ),
            TonalFamily(
                "main_engine", (1, 2, 3), 146.0, 4.0, fundamental_hz=61.0,
                modes=("cruise", "dash"),
            ),
            TonalFamily(
                "auxiliary", (1, 2), 132.0, 5.0, fundamental_hz=60.0,
                modes=("quiet_patrol",),
            ),
        ),
        _wash(BroadbandShape("machinery_wash", 20.0, 500.0, 158.0, reference_speed_knots=14.0)),
    ),
    "tern-fishing-vessel-v1": SignatureTemplate(
        _tones(
            TonalFamily(
                "blade_rate", (1, 2, 3, 4), 140.0, 3.0,
                hz_per_knot=1.32, minimum_speed_knots=1.0,
                reference_speed_knots=8.0, speed_level_exponent_db=10.0,
            ),
            TonalFamily(
                "engine_order", (1, 2), 140.0, 5.0, fundamental_hz=52.0,
            ),
            TonalFamily(
                "gear", (1,), 134.0, 0.0, fundamental_hz=2200.0,
                modes=("fishing",),
            ),
        ),
        _wash(BroadbandShape("machinery_wash", 30.0, 2800.0, 154.0, reference_speed_knots=8.0)),
    ),
    "pelagic-biologic-group-v1": SignatureTemplate(
        _tones(
            TonalFamily(
                "moan", (1, 2), 130.0, 6.0, fundamental_hz=52.0,
                modes=("vocalizing",),
            ),
            TonalFamily(
                "formant", (1, 2, 3), 136.0, 4.0, fundamental_hz=1200.0,
                modes=("vocalizing",),
            ),
            TonalFamily(
                "click", (1,), 132.0, 0.0, fundamental_hz=2200.0,
                modes=("vocalizing", "foraging"),
            ),
            TonalFamily(
                "sparse_call", (1,), 118.0, 0.0, fundamental_hz=2400.0,
                modes=("quiet_transit",),
            ),
        ),
        _wash(
            BroadbandShape(
                "forage_wash", 400.0, 4500.0, 148.0,
                modes=("foraging", "vocalizing"), reference_speed_knots=3.0,
                speed_level_exponent_db=6.0,
            ),
        ),
    ),
}


def public_model():
    """Machine-readable measurement contract. It does not list class frequencies."""
    return {
        "numeric_frequencies": True,
        "identity_lookup": False,
        "units": {
            "frequency": "Hz",
            "uncertainty": "Hz, one standard deviation",
            "source_level": acoustics.SOURCE_REFERENCE,
            "noise_level": acoustics.PRESSURE_REFERENCE,
        },
        "correlation_window_minutes": CORRELATION_WINDOW_MINUTES,
        "signal_excess": (
            "SL - TL - NL + DI - DT, plus the receiver's published frequency "
            "response. TL is the shared transmission-loss model at the line "
            "frequency and that receiver's depth. NL is ambient plus that "
            "receiver's self-noise, power-summed with broadband from the same "
            "emitter when that broadband covers the line. DT is "
            f"{NARROWBAND_DT_DB:g} dB for a line and {BROADBAND_DT_DB:g} dB "
            "for an analysis band."
        ),
        "detection_probability": (
            "The logistic of signal excess used for other reception, including "
            "its ceiling and the absence of an independent detection floor."
        ),
        "doppler": (
            "The reported center, before noise, is f * (c + v_receiver) / "
            "(c + v_source) on the horizontal line of sight. c is the true "
            "sound speed at receiver depth. v_receiver is positive toward the "
            "source and v_source is positive away from the receiver. Vertical "
            "Doppler is not modeled."
        ),
        "shared_errors": (
            "Each "
            f"{CORRELATION_WINDOW_MINUTES:g}-minute window draws one "
            "environmental quality offset, applied to every contact and every "
            "receiver, and one fractional frequency bias of "
            f"±{BIAS_FRACTION_BOUND:g}, applied to every measured line. Each "
            "receiver adds its own calibration bias of "
            f"±{ARRAY_BIAS_FRACTION_BOUND:g}. A later look in that window on "
            "the same receiver reuses those draws. Re-reading a stored report "
            "does not draw again."
        ),
        "uncertainty": (
            "Reported one-sigma frequency is the root-sum-square of "
            "1/(T*sqrt(2*SNR)), the root-mean-square of the environmental and "
            "array fractional biases, and "
            f"f*{MOTION_UNCERTAINTY_KNOTS:g} kn / c for unresolved "
            "radial motion. SNR is the processing ratio SL - TL - NL + DI, "
            "including the shared window offset and the receiver frequency "
            "response. T is the integration time."
        ),
        "integration_seconds": dict(INTEGRATION_SECONDS),
        "analysis_bands_hz": [
            {"low_hz": low, "high_hz": high} for low, high in ANALYSIS_BANDS
        ],
        "operating_state_rules": [
            "A tonal family or broadband shape is emitted only in its listed operating modes.",
            "A blade-rate fundamental is the family's hertz-per-knot coefficient times speed, and is absent below its minimum speed.",
            "Harmonics are integer multiples of the emitted fundamental.",
            "Source level adds 10 log10 of the mode's relative noise, an optional speed term, harmonic rolloff, and the emitter's persistent level bias.",
        ],
        "persistence": (
            "Initialization draws one fractional frequency offset and one "
            "level bias per emitter. Operating state does not reroll them."
        ),
        "separation": (
            "A contact report contains measured frequency, uncertainty and "
            "quality. Emitted frequency, family and offset remain hidden "
            "until the debrief."
        ),
        "opposing_detection": (
            "Opposing detection of own ship still uses the speed-based "
            "broadband source level. It does not consume this narrowband spectrum."
        ),
        "feature_likelihoods": {
            name: {"surface": weights[0], "submerged": weights[1], "biologic": weights[2]}
            for name, weights in FEATURE_LIKELIHOOD.items()
        },
        "feature_note": (
            "Crew assessments update once per correlation window from the "
            "latest measured spectral feature and cite that report's measured "
            "lines, uncertainty and quality. Every feature has likelihood in "
            "every contact domain. A frequency does not select an identity. "
            "The two roles share those reports; a second reading is not new evidence."
        ),
    }


def assign_persistent_signature(entity, dice):
    """Draw the emitter's permanent offset. Safe to store; not a public field."""
    entity["signature"] = {
        "offset_fraction": dice.between(
            f"signature-offset:{entity['id']}", -0.012, 0.012
        ),
        "level_bias_db": dice.between(
            f"signature-level:{entity['id']}", -1.5, 1.5
        ),
    }


def _signature_of(entity):
    stored = entity.get("signature") or {}
    return {
        "offset_fraction": stored.get("offset_fraction", 0.0),
        "level_bias_db": stored.get("level_bias_db", 0.0),
    }


def _mode_allows(rule, mode):
    return rule.modes is None or mode in rule.modes


def _level_adjustment(rule, relative_noise, speed, bias_db):
    adjustment = bias_db
    if rule.level_follows_mode_noise:
        adjustment += 10.0 * math.log10(max(relative_noise, 0.05))
    if rule.speed_level_exponent_db:
        adjustment += rule.speed_level_exponent_db * math.log10(
            max(speed, 0.5) / rule.reference_speed_knots
        )
    return adjustment


def emitted_components(entity):
    """Hidden spectrum for the entity's current mode and speed.

    The result is a pure function of the specification, operating state and
    the persistent signature. It does not draw a random number.
    """
    spec = get_spec(entity["spec"])
    template = SIGNATURES[spec.identifier]
    signature = _signature_of(entity)
    mode = entity["operating_mode"]
    speed = entity["speed"]
    relative = spec.mode(mode).relative_noise
    lines = []
    for family in template.families:
        if not _mode_allows(family, mode) or speed < family.minimum_speed_knots:
            continue
        if family.hz_per_knot is None:
            fundamental = family.fundamental_hz
        else:
            fundamental = family.hz_per_knot * speed
        fundamental *= 1.0 + signature["offset_fraction"]
        adjustment = _level_adjustment(
            family, relative, speed, signature["level_bias_db"]
        )
        for harmonic in family.harmonics:
            lines.append({
                "line_id": f"{family.identifier}:{harmonic}",
                "family": family.identifier,
                "harmonic": harmonic,
                "emitted_hz": fundamental * harmonic,
                "source_level_db": (
                    family.fundamental_source_level_db
                    - family.harmonic_rolloff_db * (harmonic - 1)
                    + adjustment
                ),
            })
    broadband = []
    for shape in template.broadband:
        if not _mode_allows(shape, mode):
            continue
        adjustment = _level_adjustment(
            shape, relative, speed, signature["level_bias_db"]
        )
        broadband.append({
            "family": shape.identifier,
            "low_hz": shape.low_hz,
            "high_hz": shape.high_hz,
            "band_level_db": shape.band_level_db + adjustment,
        })
    return {
        "offset_fraction": signature["offset_fraction"],
        "level_bias_db": signature["level_bias_db"],
        "lines": lines,
        "broadband": broadband,
    }


def truth_snapshot(entity):
    """Debrief view of what this emitter is radiating now."""
    emitted = emitted_components(entity)
    return {
        "offset_fraction": emitted["offset_fraction"],
        "level_bias_db": emitted["level_bias_db"],
        "operating_mode": entity["operating_mode"],
        "speed_knots": entity["speed"],
        "lines": [
            {
                "family": line["family"],
                "harmonic": line["harmonic"],
                "emitted_hz": round(line["emitted_hz"], 4),
                "source_level_db": round(line["source_level_db"], 2),
            }
            for line in emitted["lines"]
        ],
        "broadband": [
            {
                "family": item["family"],
                "low_hz": item["low_hz"],
                "high_hz": item["high_hz"],
                "band_level_db": round(item["band_level_db"], 2),
            }
            for item in emitted["broadband"]
        ],
    }


def nominal_line_hz(spec_identifier, family_identifier, harmonic, speed_knots):
    """Class nominal frequency before the individual offset. For tests and review."""
    template = SIGNATURES[spec_identifier]
    family = next(item for item in template.families if item.identifier == family_identifier)
    fundamental = (
        family.fundamental_hz if family.hz_per_knot is None
        else family.hz_per_knot * speed_knots
    )
    return fundamental * harmonic


def window_index(elapsed_minutes):
    return int(elapsed_minutes) // CORRELATION_WINDOW_MINUTES


def window_fractional_bias(dice, elapsed_minutes, array_id=None):
    """Environmental bias plus an optional per-receiver calibration bias."""
    shared = dice.between(
        f"spectrum-bias:{window_index(elapsed_minutes)}",
        -BIAS_FRACTION_BOUND,
        BIAS_FRACTION_BOUND,
    )
    if not array_id:
        return shared
    array_bias = dice.between(
        f"spectrum-bias:{window_index(elapsed_minutes)}:{array_id}",
        -ARRAY_BIAS_FRACTION_BOUND,
        ARRAY_BIAS_FRACTION_BOUND,
    )
    return shared + array_bias


def line_error_sample(dice, emitter_id, line_id, elapsed_minutes, array_id="hull"):
    """Zero-mean unit-variance processing sample, fixed for one line, receiver and window.

    The stored draw is uniform on [-1, 1]. That distribution has standard
    deviation 1/sqrt(3), so the sample is scaled by sqrt(3). Multiplying the
    result by the Cramér–Rao term then has the one-sigma width the report states.
    """
    uniform = dice.between(
        f"spectrum-line:{emitter_id}:{line_id}:{array_id}:{window_index(elapsed_minutes)}",
        -1.0,
        1.0,
    )
    return uniform * math.sqrt(3.0)


def horizontal_velocity_m_s(entity):
    """East and north velocity. Course 0 is north, matching the movement model."""
    radians = math.radians(entity["course"])
    speed = entity["speed"] * KNOTS_TO_METERS_PER_SECOND
    return speed * math.sin(radians), speed * math.cos(radians)


def doppler_frequency_hz(emitted_hz, source, receiver, sound_speed_m_s):
    """Horizontal Doppler. Zero range leaves the emitted frequency unchanged."""
    east = source["x"] - receiver["x"]
    north = source["y"] - receiver["y"]
    span = math.hypot(east, north)
    if span < 1e-9:
        return emitted_hz
    unit_east, unit_north = east / span, north / span
    source_east, source_north = horizontal_velocity_m_s(source)
    receiver_east, receiver_north = horizontal_velocity_m_s(receiver)
    source_radial = source_east * unit_east + source_north * unit_north
    receiver_radial = receiver_east * unit_east + receiver_north * unit_north
    return emitted_hz * (sound_speed_m_s + receiver_radial) / (
        sound_speed_m_s + source_radial
    )


def processing_snr_db(source_level_db, transmission_loss_db, noise_level_db, directivity_db):
    if transmission_loss_db is None:
        return -80.0
    return source_level_db - transmission_loss_db - noise_level_db + directivity_db


def cramer_rao_hz(snr_db, integration_seconds):
    """One-sigma tone uncertainty in Hz from integration time and processing SNR."""
    snr_linear = max(10.0 ** (snr_db / 10.0), 1e-4)
    return 1.0 / (integration_seconds * math.sqrt(2.0 * snr_linear))


def frequency_uncertainty_hz(frequency_hz, snr_db, integration_seconds, sound_speed_m_s):
    sigma_cr = cramer_rao_hz(snr_db, integration_seconds)
    sigma_bias = frequency_hz * combined_bias_fraction_bound() / math.sqrt(3.0)
    sigma_motion = (
        frequency_hz
        * (MOTION_UNCERTAINTY_KNOTS * KNOTS_TO_METERS_PER_SECOND)
        / sound_speed_m_s
    )
    return math.sqrt(sigma_cr ** 2 + sigma_bias ** 2 + sigma_motion ** 2)


def integration_seconds(mode, focused):
    if mode == "focus":
        return INTEGRATION_SECONDS["focus_on_contact" if focused else "focus_elsewhere"]
    if mode == "active":
        return INTEGRATION_SECONDS["active"]
    return INTEGRATION_SECONDS["listen"]


def _power_sum_db(levels):
    finite = [level for level in levels if level is not None and math.isfinite(level)]
    if not finite:
        return None
    peak = max(finite)
    return peak + 10.0 * math.log10(sum(10.0 ** ((level - peak) / 10.0) for level in finite))


def _band_overlap(low_a, high_a, low_b, high_b):
    low = max(low_a, low_b)
    high = min(high_a, high_b)
    if high <= low:
        return 0.0, None
    return high - low, 0.5 * (low + high)


def _loss_db(state, source, receiver, frequency_hz, array):
    loss = acoustics.transmission_loss(
        state["environment"]["true"],
        _range_nm(source, receiver),
        source["depth"],
        array["depth_feet"],
        frequency_hz,
    )
    return loss.transmission_loss_db


def _range_nm(source, receiver):
    return math.hypot(source["x"] - receiver["x"], source["y"] - receiver["y"])


def _receiver_noise_db(state, receiver, frequency_hz, own_ship, array_spec=None, unstable=False):
    pump = bool(state.get("pump_fault")) and receiver.get("id") == own_ship.get("id")
    return acoustics.noise_level_db(
        frequency_hz,
        receiver["speed"],
        pump,
        array_spec=array_spec,
        unstable=unstable,
    )


def _masking_db(state, source, receiver, broadband, frequency_hz, array):
    densities = []
    for shape in broadband:
        if not (shape["low_hz"] <= frequency_hz <= shape["high_hz"]):
            continue
        bandwidth = max(shape["high_hz"] - shape["low_hz"], 1.0)
        spectrum_level = shape["band_level_db"] - 10.0 * math.log10(bandwidth)
        loss = _loss_db(state, source, receiver, frequency_hz, array)
        if loss is None:
            continue
        densities.append(spectrum_level - loss)
    return _power_sum_db(densities)


def _component_excess(source_level, loss_db, noise_db, directivity, threshold, window_db):
    excess = acoustics.signal_excess_db(
        source_level, loss_db, noise_db, directivity, threshold
    )
    return excess + window_db


def measure_contact(
    state,
    source,
    receiver,
    mode,
    dice,
    extra_di=0.0,
    focused=False,
    window_db=0.0,
    array=None,
    array_spec=None,
    unstable=False,
):
    """Measure the current emitted spectrum. Random draws are keyed by window.

    Calling this again in the same window reuses the bias, the per-line error
    sample and the detection draw. It does not invent lines the emitter lacks.
    Geometry can still change the Doppler center and which of those fixed
    draws are above the detection probability.
    """
    emitted = emitted_components(source)
    elapsed = state["t"]
    water = state["environment"]["true"]["water_depth_feet"]
    if array_spec is None:
        array_id = None if array is None else array.get("id")
        array_spec = arrays.array_spec(receiver.get("spec"), array_id)
    if array is None:
        array = arrays.snapshot(
            array_spec, receiver, water_depth_feet=water, unstable=unstable,
        )
    array_id = array["id"]
    bias = window_fractional_bias(dice, elapsed, array_id)
    sound_speed = acoustics.interpolate_ssp(
        state["environment"]["true"]["sound_speed_profile"],
        acoustics.feet_to_meters(array["depth_feet"]),
    )
    duration = integration_seconds(mode, focused)
    detailed_lines = []
    for line in emitted["lines"]:
        frequency = line["emitted_hz"]
        gain = arrays.frequency_gain_db(array_spec, frequency)
        directivity = array["directivity_index_db"] + extra_di + gain
        loss = _loss_db(state, source, receiver, frequency, array)
        ambient = _receiver_noise_db(
            state, receiver, frequency, state["own"], array_spec, unstable,
        )
        masking = _masking_db(
            state, source, receiver, emitted["broadband"], frequency, array,
        )
        noise = ambient if masking is None else _power_sum_db([ambient, masking])
        snr = processing_snr_db(line["source_level_db"], loss, noise, directivity) + window_db
        excess = _component_excess(
            line["source_level_db"], loss, noise, directivity, NARROWBAND_DT_DB, window_db
        )
        sample = line_error_sample(dice, source["id"], line["line_id"], elapsed, array_id)
        detected = dice.u(
            f"spectrum-detect:{source['id']}:{line['line_id']}:{array_id}:{window_index(elapsed)}"
        ) < acoustics.detection_probability(excess)
        sigma_cr = cramer_rao_hz(snr, duration)
        uncertainty = frequency_uncertainty_hz(frequency, snr, duration, sound_speed)
        doppler = doppler_frequency_hz(frequency, source, receiver, sound_speed)
        detailed_lines.append({
            "line_id": line["line_id"],
            "family": line["family"],
            "emitted_hz": frequency,
            "doppler_hz": doppler,
            "shared_bias_fraction": bias,
            "processing_error_hz": sample * sigma_cr,
            "measured_hz": doppler * (1.0 + bias) + sample * sigma_cr,
            "uncertainty_hz": uncertainty,
            "quality": acoustics.reception_strength(excess),
            "excess_db": excess,
            "detected": detected,
        })
    detailed_bands = []
    for low, high in ANALYSIS_BANDS:
        parts = []
        for shape in emitted["broadband"]:
            width, center = _band_overlap(shape["low_hz"], shape["high_hz"], low, high)
            if width <= 0:
                continue
            emitted_width = max(shape["high_hz"] - shape["low_hz"], 1.0)
            source_level = shape["band_level_db"] + 10.0 * math.log10(width / emitted_width)
            loss = _loss_db(state, source, receiver, center, array)
            if loss is None:
                continue
            parts.append(source_level - loss)
        if not parts:
            continue
        # `received` is already SL - TL for the overlap, so the excess call
        # uses that band level with no further spreading loss. NL is the same
        # per-frequency noise level the tonal lines use, taken at the analysis
        # band's center. This rules version does not integrate noise density
        # across the band.
        received = _power_sum_db(parts)
        center = 0.5 * (low + high)
        gain = arrays.frequency_gain_db(array_spec, center)
        directivity = array["directivity_index_db"] + extra_di + gain
        noise = _receiver_noise_db(
            state, receiver, center, state["own"], array_spec, unstable,
        )
        excess = _component_excess(
            received, 0.0, noise, directivity, BROADBAND_DT_DB, window_db,
        )
        detected = dice.u(
            f"spectrum-broadband:{source['id']}:{int(low)}:{array_id}:{window_index(elapsed)}"
        ) < acoustics.detection_probability(excess)
        detailed_bands.append({
            "low_hz": low,
            "high_hz": high,
            "excess_db": excess,
            "quality": acoustics.reception_strength(excess),
            "detected": detected,
        })
    return _publish(detailed_lines, detailed_bands, emitted)


def _quantize_hz(value):
    return round(value, 3)


def _publish(detailed_lines, detailed_bands, emitted):
    public_lines = [
        {
            "measured_hz": _quantize_hz(line["measured_hz"]),
            "uncertainty_hz": max(0.001, _quantize_hz(line["uncertainty_hz"])),
            "quality": line["quality"],
        }
        for line in detailed_lines
        if line["detected"]
    ]
    public_bands = [
        {"low_hz": band["low_hz"], "high_hz": band["high_hz"], "quality": band["quality"]}
        for band in detailed_bands
        if band["detected"]
    ]
    relations = harmonic_relations(public_lines)
    public = {
        "lines": public_lines,
        "broadband": public_bands,
        "harmonic_relations": relations,
        "evidence_note": (
            "Measured frequencies are evidence. They do not identify the source."
        ),
    }
    detected_excess = [
        line["excess_db"] for line in detailed_lines if line["detected"]
    ] + [band["excess_db"] for band in detailed_bands if band["detected"]]
    best_excess = max(detected_excess) if detected_excess else None
    return {
        "public": public,
        "feature": spectral_feature(public),
        "any_detected": best_excess is not None,
        "best_excess_db": best_excess,
        "strength": (
            acoustics.reception_strength(best_excess) if best_excess is not None else "weak"
        ),
        "description": describe_spectrum(public),
        "detail": {
            "lines": detailed_lines,
            "broadband": detailed_bands,
            "emitted_line_ids": [line["line_id"] for line in emitted["lines"]],
        },
    }


def harmonic_relations(lines):
    """Integer relations among measured lines, using reported uncertainty only.

    Each detected line may be any harmonic from 1 through 8. Lines at 2f and 3f
    therefore imply the missing fundamental. A relation that treats a detected
    line as the fundamental is preferred when that simpler fit explains as many
    lines.
    """
    if len(lines) < 2:
        return []
    best = None
    best_score = None
    for line in lines:
        if line["measured_hz"] <= 0:
            continue
        for harmonic_number in range(1, 9):
            base = line["measured_hz"] / harmonic_number
            base_uncertainty = line["uncertainty_hz"] / harmonic_number
            assignments = {}
            for other in lines:
                ratio = other["measured_hz"] / base
                nearest = int(round(ratio))
                if nearest < 1 or nearest > 8:
                    continue
                residual = abs(other["measured_hz"] - nearest * base)
                tolerance = 2.0 * math.hypot(
                    other["uncertainty_hz"], nearest * base_uncertainty
                )
                if residual > max(tolerance, 0.05):
                    continue
                current = assignments.get(nearest)
                if current is None or residual < current:
                    assignments[nearest] = residual
            if len(assignments) < 2:
                continue
            multiples = sorted(assignments)
            # More assigned lines first, then the family with the smallest
            # harmonic numbers. That keeps an octave of a detected line ahead
            # of an invented lower fundamental when both fit.
            score = (len(assignments), -max(multiples))
            candidate = {"fundamental_hz": round(base, 3), "multiples": multiples}
            if best_score is None or score > best_score:
                best = candidate
                best_score = score
    return [] if best is None else [best]


def spectral_feature(public):
    """Classify the measurement the crew can see. None when nothing was resolved."""
    lines = public["lines"]
    high = [line for line in lines if line["measured_hz"] >= 1000.0]
    low = [line for line in lines if line["measured_hz"] < 1000.0]
    broadband = public["broadband"]
    if public["harmonic_relations"] and low:
        return "harmonic_set"
    if high and not low:
        return "high_band_energy"
    if low and broadband:
        return "mixed"
    if len(lines) == 1:
        return "isolated_tonal"
    if broadband and not lines:
        return "broadband_only"
    if high and low:
        return "mixed"
    return None


def describe_spectrum(public):
    """Prose built only from the measured spectrum."""
    parts = []
    if public["lines"]:
        rendered = ", ".join(
            (
                f"{line['measured_hz']:.3f} Hz ± {line['uncertainty_hz']:.3f} Hz "
                f"({line['quality']})"
            )
            for line in public["lines"]
        )
        parts.append(f"Measured lines: {rendered}.")
    else:
        parts.append("No discrete frequency line resolved.")
    if public["harmonic_relations"]:
        relation = public["harmonic_relations"][0]
        multiples = ", ".join(str(item) for item in relation["multiples"])
        parts.append(
            f"Lines are consistent with harmonics {multiples} of "
            f"{relation['fundamental_hz']:.3f} Hz."
        )
    if public["broadband"]:
        bands = ", ".join(
            f"{band['low_hz']:.0f}-{band['high_hz']:.0f} Hz ({band['quality']})"
            for band in public["broadband"]
        )
        parts.append(f"Broadband energy in {bands}.")
    parts.append("Frequencies are measurements, not an identification.")
    return " ".join(parts)
