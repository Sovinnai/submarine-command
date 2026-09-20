"""Idealized acoustic environment and frequency-dependent transmission.

The engine uses this module for every own-ship and opposing reception. It is a
bounded path-approximation model for a turn-based game, not a ray tracer and
not a claim of classified sonar performance.

Units
-----
Display and orders use feet, knots and nautical miles. Internal acoustic
calculations use SI. Transmission loss is in dB referenced to 1 m. Source and
noise levels are in dB re 1 µPa. Frequencies are in Hz.

References used for the implemented relationships
-------------------------------------------------
- Mackenzie, K. V. (1981). Nine-term equation for sound speed in the oceans.
  JASA 70(3), 807–812. Temperature in °C, practical salinity, depth in meters.
- Thorp, W. H. (1967). Analytic description of the low-frequency attenuation
  coefficient. JASA 42(1), 270. Original α is dB per kiloyard; this module
  multiplies by 1.0936 to report dB/km (1 kyd = 0.9144 km).
- Urick, R. J. Principles of Underwater Sound. Spherical spreading
  20 log10(r/1 m); cylindrical spreading after a transition range equal to the
  duct or water depth; single-image bottom reflection.
- Rayleigh two-fluid reflection for a lossless fluid bottom, plus a documented
  fictional frequency-dependent sediment loss. Grazing angle is measured from
  the horizontal.
- Constant-gradient turning range for the first convergence zone:
  R = sqrt(2 c Δz / g) from a turning point, with g = dc/dz in the deep
  positive-gradient region. This is the standard parabolic-ray identity, not a
  full PE or ray fan.

Deliberate simplifications
--------------------------
- Salinity is a uniform 35 PSU. A bathythermograph in this game measures
  temperature versus depth and computes sound speed from Mackenzie 1981.
- One representative frequency is used per contact domain until numeric
  spectra are modeled. The loss function itself accepts any frequency.
- Direct, surface-duct, half-channel, single bottom-bounce and first-CZ paths
  are evaluated independently; the lowest-loss contributing path is used.
  Coherent summation, a full image series and a ray solver are out of scope.
- Array geometry is a receiver depth and a fictional directivity index. Hull
  reception is at the platform depth; a towed array is not a receiver yet.
- Own-ship source level for opposing detection follows speed, not operating
  mode. Mode-derived radiated noise is a separate rules change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


METERS_PER_FOOT = 0.3048
METERS_PER_NAUTICAL_MILE = 1852.0
REFERENCE_RANGE_M = 1.0
SALINITY_PSU = 35.0
THORP_KYD_TO_KM = 1.0936
THORP_MAX_KHZ = 50.0
MIXED_LAYER_DROP_M_S = 1.5
DUCT_MIN_THICKNESS_M = 10.0
DUCT_MIN_CONTRAST_M_S = 2.0
CZ_WIDTH_FRACTION = 0.08
MIN_GRAZING_FOR_BOUNCE_DEG = 3.0
SPREADING_TRANSITION_FLOOR_M = 10.0

TL_REFERENCE = "dB re 1 m"
PRESSURE_REFERENCE = "dB re 1 µPa"
SOURCE_REFERENCE = "dB re 1 µPa at 1 m"

PATH_DIRECT = "direct"
PATH_SURFACE_DUCT = "surface_duct"
PATH_HALF_CHANNEL = "half_channel"
PATH_BOTTOM_BOUNCE = "bottom_bounce"
PATH_CONVERGENCE_ZONE = "convergence_zone"
PATHS = (
    PATH_DIRECT,
    PATH_SURFACE_DUCT,
    PATH_HALF_CHANNEL,
    PATH_BOTTOM_BOUNCE,
    PATH_CONVERGENCE_ZONE,
)

SUPPORTED = "supported"
UNCERTAIN = "uncertain"
OUTSIDE_SCOPE = "outside_scope"

REPRESENTATIVE_FREQUENCY_HZ = {
    "submerged": 120.0,
    "surface": 80.0,
    "biologic": 2500.0,
}
ACTIVE_FREQUENCY_HZ = 3500.0

# Fictional game source levels in the representative band, dB re 1 µPa at 1 m.
DOMAIN_SOURCE_LEVEL_DB = {
    "submerged": 118.0,
    "surface": 145.0,
    "biologic": 135.0,
}
ACTIVE_SOURCE_LEVEL_DB = 210.0
DOMAIN_TARGET_STRENGTH_DB = {
    "submerged": 10.0,
    "surface": 22.0,
    "biologic": -4.0,
}

PASSIVE_DT_DB = 6.0
ACTIVE_DT_DB = 10.0
HULL_DIRECTIVITY_DB = 8.0
OPPONENT_DIRECTIVITY_DB = 5.0
OPPONENT_DT_DB = 8.0
AMBIENT_NL_AT_1KHZ_DB = 50.0
OPPONENT_AMBIENT_NL_AT_1KHZ_DB = 52.0

BOTTOM_TYPES = {
    "mud": {
        "sound_speed_ratio": 0.98,
        "density_ratio": 1.6,
        "attenuation_db_per_khz": 1.8,
    },
    "sand": {
        "sound_speed_ratio": 1.17,
        "density_ratio": 1.9,
        "attenuation_db_per_khz": 0.8,
    },
    "rock": {
        "sound_speed_ratio": 2.3,
        "density_ratio": 2.5,
        "attenuation_db_per_khz": 0.15,
    },
}


def feet_to_meters(feet):
    return feet * METERS_PER_FOOT


def meters_to_feet(meters):
    return meters / METERS_PER_FOOT


def nm_to_meters(nautical_miles):
    return nautical_miles * METERS_PER_NAUTICAL_MILE


def sound_speed_mackenzie(temperature_c, salinity_psu, depth_m):
    """Mackenzie 1981 nine-term sound speed in m/s."""
    t = temperature_c
    s = salinity_psu
    d = depth_m
    return (
        1448.96
        + 4.591 * t
        - 5.304e-2 * t ** 2
        + 2.374e-4 * t ** 3
        + 1.340 * (s - 35.0)
        + 1.630e-2 * d
        + 1.675e-7 * d ** 2
        - 1.025e-2 * t * (s - 35.0)
        - 7.139e-13 * t * d ** 3
    )


def thorp_absorption_db_per_km(frequency_hz):
    """Thorp 1967 absorption, converted from dB/kyd to dB/km."""
    f_khz = max(frequency_hz, 1.0) / 1000.0
    f2 = f_khz * f_khz
    alpha_kyd = 0.1 * f2 / (1.0 + f2) + 40.0 * f2 / (4100.0 + f2)
    return alpha_kyd * THORP_KYD_TO_KM


def spherical_spreading_db(range_m):
    return 20.0 * math.log10(max(range_m, REFERENCE_RANGE_M) / REFERENCE_RANGE_M)


def cylindrical_spreading_db(range_m, transition_m):
    """Spherical to the transition range, cylindrical thereafter."""
    transition = max(transition_m, SPREADING_TRANSITION_FLOOR_M)
    range_m = max(range_m, REFERENCE_RANGE_M)
    if range_m <= transition:
        return spherical_spreading_db(range_m)
    return spherical_spreading_db(transition) + 10.0 * math.log10(range_m / transition)


def interpolate_ssp(ssp, depth_m):
    """Linear interpolation in depth; clamp to the sampled column."""
    if not ssp:
        raise ValueError("A sound-speed profile is required.")
    depth_m = max(ssp[0]["depth_m"], min(ssp[-1]["depth_m"], depth_m))
    for below, above in zip(ssp, ssp[1:]):
        if depth_m <= above["depth_m"]:
            span = above["depth_m"] - below["depth_m"]
            if span <= 0:
                return below["sound_speed_m_s"]
            fraction = (depth_m - below["depth_m"]) / span
            return (
                below["sound_speed_m_s"]
                + fraction * (above["sound_speed_m_s"] - below["sound_speed_m_s"])
            )
    return ssp[-1]["sound_speed_m_s"]


def _sample_point(depth_m, temperature_c):
    speed = sound_speed_mackenzie(temperature_c, SALINITY_PSU, depth_m)
    return {
        "depth_m": depth_m,
        "depth_feet": meters_to_feet(depth_m),
        "temperature_c": temperature_c,
        "sound_speed_m_s": speed,
    }


def temperature_at_depth(depth_m, mixed_layer_m, thermocline_base_m, surface_c, deep_c):
    if depth_m <= mixed_layer_m:
        return surface_c
    if depth_m >= thermocline_base_m or thermocline_base_m <= mixed_layer_m:
        return deep_c
    fraction = (depth_m - mixed_layer_m) / (thermocline_base_m - mixed_layer_m)
    return surface_c + fraction * (deep_c - surface_c)


def build_ssp(water_depth_m, mixed_layer_m, thermocline_base_m, surface_c, deep_c):
    depths = [0.0, mixed_layer_m, thermocline_base_m]
    step = 50.0
    depth = step
    while depth < water_depth_m - 1.0:
        depths.append(depth)
        depth += step
    depths.append(water_depth_m)
    unique = sorted({round(max(0.0, min(water_depth_m, item)), 6) for item in depths})
    return [
        _sample_point(
            depth,
            temperature_at_depth(
                depth, mixed_layer_m, thermocline_base_m, surface_c, deep_c
            ),
        )
        for depth in unique
    ]


def profile_structure(ssp, mixed_layer_m, thermocline_base_m, water_depth_m):
    speeds = [point["sound_speed_m_s"] for point in ssp]
    axis = min(ssp, key=lambda point: point["sound_speed_m_s"])
    surface_speed = ssp[0]["sound_speed_m_s"]
    bottom_speed = ssp[-1]["sound_speed_m_s"]
    deep_points = [point for point in ssp if point["depth_m"] >= thermocline_base_m]
    half_channel = all(
        later["sound_speed_m_s"] + 0.05 >= earlier["sound_speed_m_s"]
        for earlier, later in zip(ssp, ssp[1:])
    )
    contrast = 0.0
    if mixed_layer_m < water_depth_m:
        contrast = interpolate_ssp(ssp, mixed_layer_m) - interpolate_ssp(
            ssp, min(water_depth_m, mixed_layer_m + 20.0)
        )
    deep_gradient = 0.017
    if len(deep_points) >= 2 and deep_points[-1]["depth_m"] > deep_points[0]["depth_m"]:
        deep_gradient = max(
            1e-4,
            (deep_points[-1]["sound_speed_m_s"] - deep_points[0]["sound_speed_m_s"])
            / (deep_points[-1]["depth_m"] - deep_points[0]["depth_m"]),
        )
    return {
        "mixed_layer_m": mixed_layer_m,
        "thermocline_base_m": thermocline_base_m,
        "water_depth_m": water_depth_m,
        "axis_m": axis["depth_m"],
        "axis_speed": axis["sound_speed_m_s"],
        "surface_speed": surface_speed,
        "bottom_speed": bottom_speed,
        "half_channel": half_channel,
        "duct_contrast_m_s": contrast,
        "deep_gradient_s": deep_gradient,
        "minimum_speed": min(speeds),
        "maximum_speed": max(speeds),
    }


def conjugate_depth_m(ssp, structure, sound_speed):
    """Deepest depth below the axis at which sound speed reaches `sound_speed`."""
    if sound_speed > structure["bottom_speed"] + 0.05:
        return None
    below_axis = [
        point for point in ssp if point["depth_m"] >= structure["axis_m"] - 1e-6
    ]
    if not below_axis:
        return None
    previous = below_axis[0]
    if previous["sound_speed_m_s"] >= sound_speed:
        return previous["depth_m"]
    for point in below_axis[1:]:
        if point["sound_speed_m_s"] >= sound_speed:
            span = point["sound_speed_m_s"] - previous["sound_speed_m_s"]
            if span <= 0:
                return point["depth_m"]
            fraction = (sound_speed - previous["sound_speed_m_s"]) / span
            return previous["depth_m"] + fraction * (
                point["depth_m"] - previous["depth_m"]
            )
        previous = point
    return None


def duct_cutoff_hz(thickness_m, contrast_m_s):
    """Idealized mixed-layer cutoff: thicker or stronger ducts trap lower frequencies.

    This is a game-scale Weston-type estimate, not a mode code. It preserves the
    qualitative dependence on duct depth and sound-speed contrast.
    """
    thickness = max(thickness_m, 1.0)
    contrast = max(contrast_m_s, 0.5)
    c = 1500.0
    return 0.2 * c / thickness * math.sqrt(c / contrast)


@dataclass(frozen=True)
class PathResult:
    path: str
    status: str
    transmission_loss_db: float | None
    reason: str

    def public_dict(self):
        return {
            "path": self.path,
            "status": self.status,
            "transmission_loss_db": (
                None
                if self.transmission_loss_db is None
                else round(self.transmission_loss_db, 2)
            ),
            "reason": self.reason,
        }

    @property
    def contributes(self):
        return self.status != OUTSIDE_SCOPE and self.transmission_loss_db is not None


@dataclass(frozen=True)
class Transmission:
    frequency_hz: float
    range_m: float
    source_depth_m: float
    receiver_depth_m: float
    paths: tuple[PathResult, ...]
    selected_path: str | None
    transmission_loss_db: float | None
    reference: str = TL_REFERENCE

    def public_dict(self):
        return {
            "frequency_hz": self.frequency_hz,
            "range_m": round(self.range_m, 2),
            "source_depth_m": round(self.source_depth_m, 3),
            "receiver_depth_m": round(self.receiver_depth_m, 3),
            "paths": [path.public_dict() for path in self.paths],
            "selected_path": self.selected_path,
            "transmission_loss_db": (
                None
                if self.transmission_loss_db is None
                else round(self.transmission_loss_db, 2)
            ),
            "reference": self.reference,
        }


def _absorption(frequency_hz, range_m):
    return thorp_absorption_db_per_km(frequency_hz) * (range_m / 1000.0)


def _in_layer(depth_m, layer_m):
    return depth_m <= layer_m + 1e-6


def _direct_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz):
    slant = math.hypot(range_m, receiver_m - source_m)
    tl = spherical_spreading_db(slant) + _absorption(frequency_hz, slant)
    layer = structure["mixed_layer_m"]
    thermo = structure["thermocline_base_m"]
    crossed = (source_m > layer) != (receiver_m > layer)
    if structure["half_channel"] or not crossed:
        return PathResult(
            PATH_DIRECT, SUPPORTED, tl,
            "Geometric path with spherical spreading and Thorp absorption.",
        )
    # Downward-refracting thermocline: a shadow develops with range when the
    # two depths sit on opposite sides of the mixed layer. Reciprocity holds
    # because the test is a crossing, not a preferred source side.
    scale = max(40.0, 6.0 * max(thermo - layer, 20.0))
    shadow = 1.0 - math.exp(-range_m / scale)
    extra = (8.0 + 4.0 * math.log10(max(frequency_hz, 50.0) / 50.0)) * shadow
    tl += extra
    if shadow < 0.45:
        status, reason = SUPPORTED, "Short-range thermocline crossing; direct path still useful."
    else:
        status, reason = (
            UNCERTAIN,
            "Estimated shadow beyond a downward-refracting thermocline; diffracted remnant only.",
        )
    return PathResult(PATH_DIRECT, status, tl, reason)


def _surface_duct_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz):
    thickness = structure["mixed_layer_m"]
    contrast = structure["duct_contrast_m_s"]
    if structure["half_channel"] or thickness < DUCT_MIN_THICKNESS_M or contrast < DUCT_MIN_CONTRAST_M_S:
        return PathResult(
            PATH_SURFACE_DUCT, OUTSIDE_SCOPE, None,
            "No trapping mixed layer: surface-duct approximation does not apply.",
        )
    cutoff = duct_cutoff_hz(thickness, contrast)
    if frequency_hz < cutoff:
        return PathResult(
            PATH_SURFACE_DUCT, OUTSIDE_SCOPE, None,
            f"Frequency is below the idealized duct cutoff ({cutoff:.0f} Hz).",
        )
    both = _in_layer(source_m, thickness) and _in_layer(receiver_m, thickness)
    one = _in_layer(source_m, thickness) or _in_layer(receiver_m, thickness)
    if not one:
        return PathResult(
            PATH_SURFACE_DUCT, OUTSIDE_SCOPE, None,
            "Neither depth is in the mixed layer.",
        )
    tl = cylindrical_spreading_db(range_m, thickness) + _absorption(frequency_hz, range_m)
    if both:
        return PathResult(
            PATH_SURFACE_DUCT, SUPPORTED, tl,
            "Both depths are in the mixed layer; cylindrical spreading after the layer-depth transition.",
        )
    leakage = 12.0 + 6.0 * math.log10(max(frequency_hz, cutoff) / cutoff)
    return PathResult(
        PATH_SURFACE_DUCT, UNCERTAIN, tl + leakage,
        "One depth is below the mixed layer; only leaky duct coupling is estimated.",
    )


def _half_channel_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz):
    if not structure["half_channel"]:
        return PathResult(
            PATH_HALF_CHANNEL, OUTSIDE_SCOPE, None,
            "Sound speed does not increase through the column; half-channel trapping is out of scope.",
        )
    water = structure["water_depth_m"]
    contrast = structure["bottom_speed"] - structure["surface_speed"]
    cutoff = duct_cutoff_hz(water, max(contrast, DUCT_MIN_CONTRAST_M_S))
    if frequency_hz < cutoff:
        return PathResult(
            PATH_HALF_CHANNEL, UNCERTAIN,
            cylindrical_spreading_db(range_m, water) + _absorption(frequency_hz, range_m) + 6.0,
            "Frequency is near the half-channel cutoff; trapping is leaky.",
        )
    tl = cylindrical_spreading_db(range_m, water) + _absorption(frequency_hz, range_m)
    return PathResult(
        PATH_HALF_CHANNEL, SUPPORTED, tl,
        "Positive sound-speed gradient from surface to bottom; surface-limited cylindrical spreading.",
    )


def rayleigh_bottom_loss_db(grazing_deg, bottom, frequency_hz):
    """Positive loss from a two-fluid Rayleigh coefficient plus sediment absorption."""
    grazing = max(math.radians(grazing_deg), 1e-4)
    c_ratio = bottom["sound_speed_ratio"]
    z_ratio = bottom["density_ratio"] * c_ratio
    argument = (1.0 / c_ratio) ** 2 - math.cos(grazing) ** 2
    if argument < 0:
        reflection = 1.0
    else:
        transmitted = math.sqrt(argument)
        denom = z_ratio * math.sin(grazing) + transmitted
        if denom == 0:
            reflection = 1.0
        else:
            reflection = abs((z_ratio * math.sin(grazing) - transmitted) / denom)
    reflection = min(1.0, max(1e-6, reflection))
    interface = -20.0 * math.log10(reflection)
    sediment = bottom["attenuation_db_per_khz"] * (frequency_hz / 1000.0) / max(
        math.sin(grazing), 0.05
    )
    return interface + sediment


def _bottom_bounce_path(structure, range_m, source_m, receiver_m, frequency_hz, bottom):
    water = structure["water_depth_m"]
    if water <= max(source_m, receiver_m) + 1.0:
        return PathResult(
            PATH_BOTTOM_BOUNCE, OUTSIDE_SCOPE, None,
            "Water depth does not clear both depths; bottom bounce is out of scope.",
        )
    vertical = 2.0 * water - source_m - receiver_m
    path_length = math.hypot(range_m, vertical)
    grazing = math.degrees(math.atan2(vertical, max(range_m, 1.0)))
    if grazing < MIN_GRAZING_FOR_BOUNCE_DEG:
        return PathResult(
            PATH_BOTTOM_BOUNCE, UNCERTAIN,
            spherical_spreading_db(path_length)
            + _absorption(frequency_hz, path_length)
            + rayleigh_bottom_loss_db(grazing, bottom, frequency_hz)
            + 8.0,
            "Very low grazing angle; roughness and scatter are not modeled, so the bounce is uncertain.",
        )
    tl = (
        spherical_spreading_db(path_length)
        + _absorption(frequency_hz, path_length)
        + rayleigh_bottom_loss_db(grazing, bottom, frequency_hz)
    )
    return PathResult(
        PATH_BOTTOM_BOUNCE, SUPPORTED, tl,
        f"Single-image bottom bounce at {grazing:.1f} deg grazing with a Rayleigh fluid-bottom loss.",
    )


def _cz_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz):
    has_axis = structure["axis_m"] > structure["mixed_layer_m"] + 5.0 and not structure["half_channel"]
    if not has_axis:
        return PathResult(
            PATH_CONVERGENCE_ZONE, OUTSIDE_SCOPE, None,
            "No deep sound-channel axis; convergence-zone refraction is out of scope.",
        )
    source_c = interpolate_ssp(ssp, source_m)
    receiver_c = interpolate_ssp(ssp, receiver_m)
    source_conj = conjugate_depth_m(ssp, structure, source_c)
    receiver_conj = conjugate_depth_m(ssp, structure, receiver_c)
    if source_conj is None or receiver_conj is None:
        return PathResult(
            PATH_CONVERGENCE_ZONE, OUTSIDE_SCOPE, None,
            "No conjugate depth in this water column; CZ physics does not apply.",
        )
    g = structure["deep_gradient_s"]
    c_mean = 0.5 * (source_c + receiver_c)

    def leg(delta_z):
        return math.sqrt(max(0.0, 2.0 * c_mean * max(delta_z, 1.0) / g))

    cz_range = leg(source_conj - source_m) + leg(receiver_conj - receiver_m)
    width = CZ_WIDTH_FRACTION * cz_range + 0.3 * abs(source_m - receiver_m)
    offset = abs(range_m - cz_range)
    tl = spherical_spreading_db(range_m) + _absorption(frequency_hz, range_m) - 8.0
    if offset <= width / 2.0:
        return PathResult(
            PATH_CONVERGENCE_ZONE, SUPPORTED, tl,
            f"First CZ annulus around {cz_range / METERS_PER_NAUTICAL_MILE:.1f} nm from the conjugate-depth turning range.",
        )
    if offset <= width:
        return PathResult(
            PATH_CONVERGENCE_ZONE, UNCERTAIN, tl + 6.0,
            "Near the edge of the first CZ annulus; focusing is only an estimate.",
        )
    return PathResult(
        PATH_CONVERGENCE_ZONE, OUTSIDE_SCOPE, None,
        f"Range is outside the first CZ annulus centered near {cz_range / METERS_PER_NAUTICAL_MILE:.1f} nm.",
    )


def transmission_loss(
    environment,
    range_nm,
    source_depth_feet,
    receiver_depth_feet,
    frequency_hz,
):
    """Approximate TL for one frequency and source/receiver pair.

    `environment` is the true water column. The measured profile is never used
    for adjudication. Path status is supported, uncertain, or outside_scope.
    """
    water_m = feet_to_meters(environment["water_depth_feet"])
    source_m = min(max(feet_to_meters(source_depth_feet), 0.0), water_m)
    receiver_m = min(max(feet_to_meters(receiver_depth_feet), 0.0), water_m)
    range_m = max(nm_to_meters(range_nm), 0.0)
    ssp = environment["sound_speed_profile"]
    structure = profile_structure(
        ssp,
        feet_to_meters(environment["mixed_layer_depth_feet"]),
        feet_to_meters(environment["thermocline_base_feet"]),
        water_m,
    )
    bottom = BOTTOM_TYPES[environment["bottom"]["type"]]
    paths = (
        _direct_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz),
        _surface_duct_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz),
        _half_channel_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz),
        _bottom_bounce_path(structure, range_m, source_m, receiver_m, frequency_hz, bottom),
        _cz_path(ssp, structure, range_m, source_m, receiver_m, frequency_hz),
    )
    contributing = [path for path in paths if path.contributes]
    if contributing:
        selected = min(contributing, key=lambda path: path.transmission_loss_db)
        return Transmission(
            frequency_hz, range_m, source_m, receiver_m, paths,
            selected.path, selected.transmission_loss_db,
        )
    return Transmission(
        frequency_hz, range_m, source_m, receiver_m, paths, None, None,
    )


def representative_frequency_hz(domain, mode="passive"):
    if mode == "active":
        return ACTIVE_FREQUENCY_HZ
    return REPRESENTATIVE_FREQUENCY_HZ[domain]


def source_level_db(domain, relative_noise, mode="passive"):
    if mode == "active":
        return ACTIVE_SOURCE_LEVEL_DB
    return DOMAIN_SOURCE_LEVEL_DB[domain] + 10.0 * math.log10(max(relative_noise, 0.05))


def own_ship_source_level_db(speed_knots, active=False):
    """Speed-derived own-ship source level; operating mode is not applied here."""
    if active:
        return ACTIVE_SOURCE_LEVEL_DB
    return 132.0 + 20.0 * math.log10(max(speed_knots, 1.0) / 5.0)


def target_strength_db(domain):
    return DOMAIN_TARGET_STRENGTH_DB[domain]


def ambient_noise_db(frequency_hz, baseline=AMBIENT_NL_AT_1KHZ_DB):
    """Fictional Wenz-like slope: higher ambient level at lower frequency."""
    return baseline + 16.0 * math.log10(1000.0 / max(frequency_hz, 50.0))


def self_noise_adjustment_db(speed_knots, pump_fault=False):
    factor = max(0.24, 1.0 - max(0.0, speed_knots - 5.0) * 0.055)
    extra = 0.0 if not pump_fault else 3.5
    return -10.0 * math.log10(factor) + extra


def noise_level_db(frequency_hz, speed_knots, pump_fault=False, baseline=AMBIENT_NL_AT_1KHZ_DB):
    return ambient_noise_db(frequency_hz, baseline) + self_noise_adjustment_db(
        speed_knots, pump_fault
    )


def signal_excess_db(source_level, loss, noise_level, directivity, threshold, two_way=False, target_strength=0.0):
    if loss is None:
        return -80.0
    spreading = 2.0 * loss if two_way else loss
    return source_level - spreading + target_strength - noise_level + directivity - threshold


def detection_probability(signal_excess, floor=0.015, ceiling=0.97, scale_db=4.0):
    probability = 1.0 / (1.0 + math.exp(-signal_excess / scale_db))
    return min(ceiling, max(floor, probability))


def reception_strength(signal_excess):
    if signal_excess > 10:
        return "strong"
    if signal_excess > 0:
        return "moderate"
    return "weak"


def receiving_array(entity):
    """Hull receiver at the platform depth. Towed-array geometry is not modeled."""
    equipment = entity.get("equipment", {})
    return {
        "id": "integrated_passive",
        "depth_feet": entity["depth"],
        "directivity_index_db": HULL_DIRECTIVITY_DB,
        "state": equipment.get("integrated_passive", "available"),
        "note": (
            "Combined hull reception at present keel depth. A towed array is "
            "carried but is not a modeled receiver in this rules version."
        ),
    }


def _bottom_public(bottom_type):
    props = BOTTOM_TYPES[bottom_type]
    return {
        "type": bottom_type,
        "sound_speed_ratio": props["sound_speed_ratio"],
        "density_ratio": props["density_ratio"],
        "attenuation_db_per_khz": props["attenuation_db_per_khz"],
    }


def _public_ssp(ssp):
    return [
        {
            "depth_feet": round(point["depth_feet"], 1),
            "temperature_c": round(point["temperature_c"], 2),
            "sound_speed_m_s": round(point["sound_speed_m_s"], 2),
        }
        for point in ssp
    ]


def initialize_environment(dice):
    """Draw the true column and a dated onboard estimate."""
    water_feet = dice.between("initial:water-depth", 1900, 2800)
    mixed_feet = dice.between("initial:mixed-layer", 90, 280)
    thermo_span = dice.between("initial:thermocline-thickness", 150, 420)
    thermo_feet = min(water_feet - 200, mixed_feet + thermo_span)
    surface_c = dice.between("initial:sst", 14.0, 22.0)
    deep_c = dice.between("initial:deep-temp", 4.5, 8.5)
    bottom_type = dice.choose(
        "initial:bottom", ["mud", "sand", "rock"], [0.35, 0.45, 0.20]
    )
    true_ssp = build_ssp(
        feet_to_meters(water_feet),
        feet_to_meters(mixed_feet),
        feet_to_meters(thermo_feet),
        surface_c,
        deep_c,
    )
    age_at_zero = dice.between("initial:profile-age", 25, 180)
    temp_error = dice.between("initial:bt-error", -1.4, 1.4)
    layer_error = dice.between("initial:layer-error", -45, 45)
    measured_mixed = min(max(mixed_feet + layer_error, 40.0), water_feet - 400)
    measured_thermo = min(max(thermo_feet + 0.4 * layer_error, measured_mixed + 80), water_feet - 80)
    measured_surface = surface_c + temp_error
    measured_deep = deep_c + 0.4 * temp_error
    charted_depth = max(900.0, round((water_feet + dice.between("initial:chart-error", -40, 40)) / 50.0) * 50.0)
    charted_bottom = (
        bottom_type
        if dice.u("initial:bottom-chart") < 0.82
        else dice.choose(
            "initial:bottom-chart-error",
            [name for name in BOTTOM_TYPES if name != bottom_type],
        )
    )
    measured_ssp = build_ssp(
        feet_to_meters(charted_depth),
        feet_to_meters(measured_mixed),
        feet_to_meters(measured_thermo),
        measured_surface,
        measured_deep,
    )
    true = {
        "water_depth_feet": water_feet,
        "mixed_layer_depth_feet": mixed_feet,
        "thermocline_base_feet": thermo_feet,
        "surface_temperature_c": surface_c,
        "deep_temperature_c": deep_c,
        "salinity_psu": SALINITY_PSU,
        "bottom": _bottom_public(bottom_type),
        "sound_speed_profile": true_ssp,
    }
    measured = {
        "water_depth_feet": charted_depth,
        "mixed_layer_depth_feet": measured_mixed,
        "thermocline_base_feet": measured_thermo,
        "surface_temperature_c": measured_surface,
        "deep_temperature_c": measured_deep,
        "salinity_psu": SALINITY_PSU,
        "bottom": _bottom_public(charted_bottom),
        "sound_speed_profile": measured_ssp,
        "age_at_elapsed_zero_minutes": age_at_zero,
        "instrument": "bathythermograph temperature profile with Mackenzie sound speed",
    }
    return {"true": true, "measured": measured}


def profile_age_minutes(environment, elapsed_minutes):
    return environment["measured"]["age_at_elapsed_zero_minutes"] + elapsed_minutes


def path_scope_from_environment(column, frequency_hz=120.0):
    """Describe which path families the model will evaluate for this column.

    Uses the supplied column, which must be the measured estimate when shown
    to the captain. Geometry-specific support still depends on range and the
    two depths.
    """
    mixed = max(column["mixed_layer_depth_feet"] * 0.5, 20.0)
    probe = transmission_loss(column, 8.0, mixed, mixed, frequency_hz)
    cz_probe = transmission_loss(column, 32.0, 80.0, 80.0, frequency_hz)
    cz = next(path for path in cz_probe.paths if path.path == PATH_CONVERGENCE_ZONE)
    if "annulus" in cz.reason:
        cz_status, cz_reason = SUPPORTED, (
            "A conjugate depth exists in this estimated column; CZ is evaluated "
            "only inside its annulus."
        )
    else:
        cz_status, cz_reason = cz.status, cz.reason
    scope = {
        path.path: {"status": path.status, "reason": path.reason}
        for path in probe.paths
        if path.path != PATH_CONVERGENCE_ZONE
    }
    scope[PATH_CONVERGENCE_ZONE] = {"status": cz_status, "reason": cz_reason}
    return scope


def public_environment(environment, elapsed_minutes):
    measured = environment["measured"]
    age = profile_age_minutes(environment, elapsed_minutes)
    scope = path_scope_from_environment(measured)
    return {
        "profile_age_minutes": round(age, 1),
        "measured_at": (
            "Last onboard bathythermograph; sound speed from Mackenzie 1981 "
            f"at {SALINITY_PSU:g} PSU. This is an estimate, not a live profile."
        ),
        "charted_water_depth_feet": round(measured["water_depth_feet"], 1),
        "charted_bottom": measured["bottom"]["type"],
        "estimated_mixed_layer_depth_feet": round(measured["mixed_layer_depth_feet"], 1),
        "estimated_thermocline_base_feet": round(measured["thermocline_base_feet"], 1),
        "sound_speed_profile": _public_ssp(measured["sound_speed_profile"]),
        "units": {
            "depth": "feet",
            "sound_speed": "m/s",
            "temperature": "°C",
            "frequency": "Hz",
            "transmission_loss": TL_REFERENCE,
            "source_level": SOURCE_REFERENCE,
            "noise_level": PRESSURE_REFERENCE,
        },
        "path_model": scope,
        "receiver": (
            "Hull array at present keel depth. Changing depth uses this same "
            "estimated column; the engine adjudicates with the hidden true column."
        ),
    }


def environment_report_text(environment, elapsed_minutes=0):
    public = public_environment(environment, elapsed_minutes)
    cz = public["path_model"][PATH_CONVERGENCE_ZONE]
    cz_text = (
        "First CZ is outside the model's scope for the charted water column."
        if cz["status"] == OUTSIDE_SCOPE
        else "A first CZ annulus may exist in the estimated column."
    )
    return (
        f"Last onboard sound-speed profile is {public['profile_age_minutes']:.0f} minutes old. "
        f"Estimated mixed layer near {public['estimated_mixed_layer_depth_feet']:.0f} feet, "
        f"thermocline to about {public['estimated_thermocline_base_feet']:.0f} feet. "
        f"Charted water depth {public['charted_water_depth_feet']:.0f} feet, "
        f"{public['charted_bottom']} bottom. {cz_text} "
        "Conditions are an estimate, not a live measurement."
    )


def deep_water_test_environment(water_depth_feet=13100, mixed_layer_feet=180, thermocline_base_feet=2200,
                                surface_c=18.0, deep_c=4.0, bottom_type="sand"):
    """A documented deep-water column for CZ limiting-case tests."""
    ssp = build_ssp(
        feet_to_meters(water_depth_feet),
        feet_to_meters(mixed_layer_feet),
        feet_to_meters(thermocline_base_feet),
        surface_c,
        deep_c,
    )
    return {
        "water_depth_feet": water_depth_feet,
        "mixed_layer_depth_feet": mixed_layer_feet,
        "thermocline_base_feet": thermocline_base_feet,
        "surface_temperature_c": surface_c,
        "deep_temperature_c": deep_c,
        "salinity_psu": SALINITY_PSU,
        "bottom": _bottom_public(bottom_type),
        "sound_speed_profile": ssp,
    }


def winter_half_channel_environment(water_depth_feet=2400, surface_c=6.0, bottom_type="sand"):
    """Nearly isothermal cold column so pressure makes a half-channel."""
    water_m = feet_to_meters(water_depth_feet)
    ssp = build_ssp(water_m, water_m, water_m, surface_c, surface_c)
    return {
        "water_depth_feet": water_depth_feet,
        "mixed_layer_depth_feet": water_depth_feet,
        "thermocline_base_feet": water_depth_feet,
        "surface_temperature_c": surface_c,
        "deep_temperature_c": surface_c,
        "salinity_psu": SALINITY_PSU,
        "bottom": _bottom_public(bottom_type),
        "sound_speed_profile": ssp,
    }


def capability_environment():
    return {
        "implemented": [
            "sound-speed profile from a temperature column and Mackenzie 1981",
            "water depth and fluid-bottom properties",
            "frequency-dependent Thorp absorption",
            "direct, surface-duct, half-channel, bottom-bounce and first-CZ path approximations",
            "distinct true column versus a dated onboard estimate",
        ],
        "full_sound_speed_profile_modeled": True,
        "convergence_zone_modeled": True,
        "bottom_bounce_modeled": True,
        "half_channel_modeled": True,
        "ray_solver_modeled": False,
        "bathythermograph_order_modeled": False,
        "units": {
            "transmission_loss": TL_REFERENCE,
            "source_level": SOURCE_REFERENCE,
            "noise_level": PRESSURE_REFERENCE,
            "frequency": "Hz",
            "depth": "feet",
        },
        "path_status": [SUPPORTED, UNCERTAIN, OUTSIDE_SCOPE],
        "description": (
            "Transmission loss is the lowest-loss contributing path among the "
            "documented approximations. A path is reported supported, uncertain "
            "or outside the model's scope; CZ, bottom bounce or a half-channel "
            "are never claimed from a narrative label alone. Own-ship and "
            "opposing reception use the same true column. The captain sees only "
            "the dated estimate. Computation is closed-form; there is no ray fan."
        ),
    }
