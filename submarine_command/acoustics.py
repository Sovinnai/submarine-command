"""Idealized ocean acoustics: units, reference levels, paths and the sonar equation.

Internal computation is SI (metres, hertz, seconds, decibels). The engine
converts to the game's feet, knots and nautical miles at the report boundary.

Reference levels used everywhere in this package:

    Source level      SL   dB re 1 micropascal at 1 m, in a band's analysis bandwidth
    Noise level       NL   dB re 1 micropascal, in the same analysis bandwidth
    Transmission loss TL   dB, relative to the same 1 m reference
    Array gain        DI   dB
    Detection threshold DT dB of signal-to-noise ratio required at the array
    Target strength   TS   dB

    Passive   SE = SL - TL - (NL - DI) - DT
    Active    SE = SL - 2*TL + TS - (NL - DI) - DT

Signal excess SE is the single quantity every detection decision in the game is
made from. Nothing else may invent an audibility scale of its own.

The closed-form relations (Thorp absorption, ray-curvature limiting range, duct
cutoff frequency, image-source boundary reflection) are standard textbook
approximations. Every coefficient that is not a physical constant is a fictional
game parameter. docs/ACOUSTICS.md records each formula, its source, its validity
limits and the assumptions behind the tuned values.

This module is pure: it holds no world state, draws no random numbers and
mutates nothing. Values that the engine stores or compares against a random draw
are quantised here so that replay does not depend on a platform's last-bit
floating point behaviour.
"""
from dataclasses import dataclass
import math

METRES_PER_FOOT = 0.3048
METRES_PER_NAUTICAL_MILE = 1852.0
METRES_PER_KILOYARD = 914.4
NOMINAL_SOUND_SPEED = 1500.0

# Detection probability is a cumulative normal in signal excess: 50 per cent at
# SE = 0. The spread absorbs everything the model does not resolve.
SIGNAL_EXCESS_SIGMA_DB = 8.0
MINIMUM_DETECTION_PROBABILITY = 0.015
MAXIMUM_DETECTION_PROBABILITY = 0.97

# Shadow-zone leakage. A parametric fit, not a diffraction solution: the excess
# loss over spherical spreading ramps to a plateau beyond the refracted path's
# limiting range. Low frequencies leak into the shadow more readily.
SHADOW_PLATEAU_BASE_DB = 14.0
SHADOW_PLATEAU_SLOPE_DB = 6.0
SHADOW_RAMP_METRES = 3704.0

# Surface-duct leakage out of the trapped path, dB per kilometre, scaled by how
# far the frequency sits above the duct's cutoff.
DUCT_LEAKAGE_DB_PER_KM = 0.2
DUCT_CUTOFF_UNCERTAIN_RATIO = 1.4

# Convergence zone. Implemented as a gate plus a range-keyed annulus; there is no
# ray solution behind it, so it is never reported as better than uncertain.
DEPTH_EXCESS_MARGIN_M = 300.0
CONVERGENCE_ZONE_SPACING_M = 61000.0
CONVERGENCE_ZONE_WIDTH_FRACTION = 0.08
CONVERGENCE_ZONE_GAIN_DB = 10.0

# None of these approximations are meant for basin-scale propagation. Beyond this
# range the model declines to answer rather than extrapolating a spreading law
# past anything it can stand behind.
MAXIMUM_MODELLED_RANGE_M = 60.0 * METRES_PER_NAUTICAL_MILE

# Bottom bounce is straight-ray image-source geometry. Below this grazing angle
# refraction dominates and the approximation is reported as out of scope.
BOTTOM_MINIMUM_GRAZING_DEG = 2.0
BOTTOM_CONFIDENT_GRAZING_DEG = 20.0


def quantise(value, places=3):
    """Round a derived decibel value so stored state and replay stay identical.

    Transcendental functions may differ in their last bit between platforms and
    library versions. Every value that reaches the saved world, a report or a
    comparison against a random draw passes through here first.
    """
    return round(value + 0.0, places)


@dataclass(frozen=True)
class Band:
    """One processing band. The analysis bandwidth, not the band's full width,
    sets the noise power and the detection threshold: a narrow line search and a
    broadband search in the same band are different measurements."""
    identifier: str
    low_hz: float
    high_hz: float
    centre_hz: float
    analysis_bandwidth_hz: float
    label: str

    def public_definition(self):
        return {"id": self.identifier, "low_hz": self.low_hz, "high_hz": self.high_hz,
                "centre_hz": self.centre_hz,
                "analysis_bandwidth_hz": self.analysis_bandwidth_hz, "label": self.label}


BANDS = (
    Band("b035", 20, 60, 35.0, 1.0, "very low frequency"),
    Band("b104", 60, 180, 104.0, 1.0, "low frequency"),
    Band("b300", 180, 500, 300.0, 3.0, "low-mid frequency"),
    Band("b866", 500, 1500, 866.0, 10.0, "mid frequency"),
    Band("b2739", 1500, 5000, 2739.0, 50.0, "upper-mid frequency"),
    Band("b7746", 5000, 12000, 7746.0, 200.0, "high frequency"),
)
BAND_IDS = tuple(band.identifier for band in BANDS)
BAND_BY_ID = {band.identifier: band for band in BANDS}
ACTIVE_BAND = BAND_BY_ID["b7746"]

# Wenz-shaped ambient spectrum levels, dB re 1 micropascal squared per hertz.
# Distant shipping dominates below a few hundred hertz; wind and sea surface
# dominate above it. Tabulated at shipping density 4 and sea state 3.
SHIPPING_SPECTRUM_DB = {"b035": 72.0, "b104": 66.0, "b300": 57.0,
                        "b866": 47.0, "b2739": 37.0, "b7746": 27.0}
WIND_SPECTRUM_DB = {"b035": 52.0, "b104": 54.0, "b300": 56.0,
                    "b866": 55.0, "b2739": 50.0, "b7746": 44.0}
SHIPPING_DENSITY_STEP_DB = 4.0
SEA_STATE_STEP_DB = 5.0
REFERENCE_SHIPPING_DENSITY = 4
REFERENCE_SEA_STATE = 3

# Bottom reflection loss per bounce, dB against grazing angle in degrees. A fast
# bottom reflects shallow-angle energy well and absorbs steep energy; a slow,
# fine-grained bottom loses energy at every angle. Interpolated linearly.
BOTTOM_TYPES = {
    "sand": {
        "description": "Firm sand; sound speed above the water column, so shallow grazing angles reflect well.",
        "critical_angle_deg": 31.0,
        "loss_db": ((0.0, 1.0), (5.0, 1.2), (10.0, 1.6), (20.0, 2.4),
                    (31.0, 4.2), (45.0, 9.0), (70.0, 12.0), (90.0, 13.0)),
    },
    "fine_sediment": {
        "description": "Fine sediment and clay; sound speed below the water column, so every bounce loses energy.",
        "critical_angle_deg": None,
        "loss_db": ((0.0, 3.0), (5.0, 4.0), (10.0, 5.0), (20.0, 7.0),
                    (30.0, 8.6), (45.0, 11.0), (70.0, 13.0), (90.0, 14.0)),
    },
    "rock": {
        "description": "Exposed rock; a strong reflector at nearly every angle, with scattering not modelled.",
        "critical_angle_deg": 55.0,
        "loss_db": ((0.0, 0.6), (5.0, 0.8), (10.0, 1.0), (20.0, 1.4),
                    (45.0, 2.5), (70.0, 3.2), (90.0, 3.5)),
    },
}

# Surface reflection loss per bounce for the trapped duct path.
SURFACE_LOSS_DB = 0.5

# Flow and machinery self noise referred to the array. Decibels added per decade
# of speed above a receiver's published quiet speed; a fictional game parameter
# in the range usually quoted for flow noise.
SELF_NOISE_SPEED_SLOPE_DB = 30.0

# Radiated noise against speed, decibels per decade above the speed a hull's
# signature is quoted at, and the step added once a propeller cavitates.
RADIATED_SPEED_SLOPE_DB = 35.0
CAVITATION_EXCESS_DB = 12.0

# Distant shipping noise is itself trapped in the surface duct, so a receiver
# inside the duct sits in a louder noise field. This is what a boat trades away
# when it comes above the layer to work the surface picture.
DUCT_AMBIENT_EXCESS_DB = {"b035": 5.0, "b104": 5.0, "b300": 4.0,
                          "b866": 2.0, "b2739": 1.0, "b7746": 0.0}


def feet_to_metres(feet):
    return feet * METRES_PER_FOOT


def metres_to_feet(metres):
    return metres / METRES_PER_FOOT


def nautical_miles_to_metres(nm):
    return nm * METRES_PER_NAUTICAL_MILE


def metres_to_nautical_miles(metres):
    return metres / METRES_PER_NAUTICAL_MILE


def absorption_db_per_metre(frequency_hz):
    """Thorp's expression for absorption in sea water, dB per kiloyard.

    Published for roughly 100 Hz to several hundred kilohertz. Below 100 Hz it
    understates absorption, which is harmless here because absorption is
    negligible against spreading at those frequencies and ranges.
    """
    kilohertz = frequency_hz / 1000.0
    squared = kilohertz * kilohertz
    per_kiloyard = (0.1 * squared / (1.0 + squared)
                    + 40.0 * squared / (4100.0 + squared)
                    + 2.75e-4 * squared
                    + 0.003)
    return per_kiloyard / METRES_PER_KILOYARD


def interpolate(table, x):
    """Linear interpolation over an increasing table of (x, y) knots."""
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def bottom_loss_db(bottom, grazing_deg):
    return interpolate(BOTTOM_TYPES[bottom]["loss_db"], abs(grazing_deg))


@dataclass(frozen=True)
class SoundSpeedProfile:
    """Piecewise-linear sound speed against depth.

    The profile is a handful of control points, not a measured cast. Propagation
    never traces a ray through it; it reads the features that closed-form
    relations need: the sonic layer depth, the gradient on each side of it, the
    channel axis and the critical depth.
    """
    points: tuple

    def speed_at(self, depth_m):
        return interpolate(self.points, max(0.0, depth_m))

    def gradient_at(self, depth_m):
        depth = max(0.0, depth_m)
        for (z0, c0), (z1, c1) in zip(self.points, self.points[1:]):
            if depth <= z1:
                return (c1 - c0) / (z1 - z0) if z1 > z0 else 0.0
        (z0, c0), (z1, c1) = self.points[-2], self.points[-1]
        return (c1 - c0) / (z1 - z0) if z1 > z0 else 0.0

    @property
    def maximum_depth_m(self):
        return self.points[-1][0]

    @property
    def layer_depth_m(self):
        """Sonic layer depth: the shallowest depth at which sound speed peaks."""
        best_depth, best_speed = self.points[0]
        for depth, speed in self.points:
            if speed > best_speed:
                best_depth, best_speed = depth, speed
            elif depth > best_depth and speed < best_speed:
                break
        return best_depth

    @property
    def channel_axis_m(self):
        return min(self.points, key=lambda point: point[1])[0]

    @property
    def duct_gradient(self):
        layer = self.layer_depth_m
        return self.gradient_at(layer / 2.0) if layer > 0 else 0.0

    @property
    def below_layer_gradient(self):
        return self.gradient_at(self.layer_depth_m + 1.0)

    @property
    def has_surface_duct(self):
        return self.layer_depth_m >= 10.0 and self.duct_gradient > 0

    @property
    def duct_cutoff_hz(self):
        """Lowest frequency a surface duct of this thickness traps.

        Urick's approximation for the maximum trapped wavelength in a mixed
        layer. A thicker duct traps lower frequencies.
        """
        if not self.has_surface_duct:
            return None
        thickness = self.layer_depth_m
        return quantise(NOMINAL_SOUND_SPEED / (0.008 * thickness ** 1.5), 1)

    @property
    def critical_depth_m(self):
        """Depth below the channel axis at which sound speed returns to its
        near-surface maximum. Without it there is no depth excess and therefore
        no convergence zone."""
        axis = self.channel_axis_m
        surface_maximum = max(speed for depth, speed in self.points if depth <= axis)
        below = [point for point in self.points if point[0] >= axis]
        for (z0, c0), (z1, c1) in zip(below, below[1:]):
            if c1 >= surface_maximum:
                if c1 == c0:
                    return z1
                return z0 + (z1 - z0) * (surface_maximum - c0) / (c1 - c0)
        return None

    def public_definition(self):
        return {"control_points_depth_feet_speed_mps": [
            [round(metres_to_feet(depth), 1), round(speed, 2)] for depth, speed in self.points]}


def layered_profile(surface_speed_mps, layer_depth_m, duct_gradient,
                    thermocline_gradient, deep_gradient, thermocline_thickness_m,
                    maximum_depth_m):
    """Build a four-segment profile: mixed layer, thermocline, deep gradient."""
    layer_depth_m = max(0.0, min(layer_depth_m, maximum_depth_m - 2.0))
    surface = (0.0, surface_speed_mps)
    layer = (layer_depth_m, surface_speed_mps + duct_gradient * layer_depth_m)
    axis_depth = min(layer_depth_m + thermocline_thickness_m, maximum_depth_m - 1.0)
    axis = (axis_depth, layer[1] + thermocline_gradient * (axis_depth - layer_depth_m))
    bottom = (maximum_depth_m, axis[1] + deep_gradient * (maximum_depth_m - axis_depth))
    return SoundSpeedProfile(tuple(
        (quantise(depth, 2), quantise(speed, 3)) for depth, speed in (surface, layer, axis, bottom)))


@dataclass(frozen=True)
class Bathymetry:
    """Coarse water-depth grid in metres, bilinearly interpolated.

    Charted depth is public knowledge; only the sound speed structure is
    uncertain to the boat.
    """
    origin_east_nm: float
    origin_north_nm: float
    spacing_nm: float
    depths_m: tuple

    def depth_m(self, east_nm, north_nm):
        rows = len(self.depths_m)
        columns = len(self.depths_m[0])
        u = (east_nm - self.origin_east_nm) / self.spacing_nm
        v = (north_nm - self.origin_north_nm) / self.spacing_nm
        u = min(max(u, 0.0), columns - 1.0)
        v = min(max(v, 0.0), rows - 1.0)
        column, row = int(u), int(v)
        column = min(column, columns - 2) if columns > 1 else 0
        row = min(row, rows - 2) if rows > 1 else 0
        fu, fv = u - column, v - row
        c1 = min(column + 1, columns - 1)
        r1 = min(row + 1, rows - 1)
        top = self.depths_m[row][column] * (1 - fu) + self.depths_m[row][c1] * fu
        bottom = self.depths_m[r1][column] * (1 - fu) + self.depths_m[r1][c1] * fu
        return quantise(top * (1 - fv) + bottom * fv, 2)


@dataclass(frozen=True)
class Environment:
    """Conditions along one path, sampled at its midpoint.

    The model is range independent within a single transmission-loss call. Where
    the real passage changes between the two platforms, this is an
    approximation, and it is recorded as one.
    """
    profile: SoundSpeedProfile
    water_depth_m: float
    bottom: str
    sea_state: int
    shipping_density: int

    def public_definition(self):
        return {"sound_speed_profile": self.profile.public_definition(),
                "water_depth_feet": round(metres_to_feet(self.water_depth_m)),
                "layer_depth_feet": round(metres_to_feet(self.profile.layer_depth_m)),
                "bottom": self.bottom,
                "bottom_description": BOTTOM_TYPES[self.bottom]["description"],
                "sea_state": self.sea_state,
                "shipping_density": self.shipping_density}


@dataclass(frozen=True)
class OceanModel:
    """The true conditions across the operating area.

    The sound speed structure varies slowly along the passage and with time; the
    charted depth comes from the bathymetry grid. Nothing here is random: the
    scenario draws the parameters once, and the field is then a deterministic
    function of place and elapsed time, so an ageing profile estimate drifts away
    from truth without any further draw.
    """
    bathymetry: Bathymetry
    bottom: str
    sea_state: int
    shipping_density: int
    surface_speed_mps: float
    layer_depth_west_m: float
    layer_depth_east_m: float
    layer_variation_m: float
    layer_period_minutes: float
    west_east_span_nm: float
    duct_gradient: float
    thermocline_gradient: float
    deep_gradient: float
    thermocline_thickness_m: float

    def layer_depth_m(self, east_nm, minutes):
        fraction = min(max(east_nm / self.west_east_span_nm, 0.0), 1.0)
        along = self.layer_depth_west_m + (self.layer_depth_east_m - self.layer_depth_west_m) * fraction
        swing = self.layer_variation_m * math.sin(2.0 * math.pi * minutes / self.layer_period_minutes)
        return quantise(along + swing, 2)

    def profile_at(self, east_nm, north_nm, minutes):
        return layered_profile(
            self.surface_speed_mps,
            self.layer_depth_m(east_nm, minutes),
            self.duct_gradient,
            self.thermocline_gradient,
            self.deep_gradient,
            self.thermocline_thickness_m,
            self.bathymetry.depth_m(east_nm, north_nm),
        )

    def at(self, east_nm, north_nm, minutes):
        return Environment(
            self.profile_at(east_nm, north_nm, minutes),
            self.bathymetry.depth_m(east_nm, north_nm),
            self.bottom,
            self.sea_state,
            self.shipping_density,
        )


def radius_of_curvature_m(gradient):
    """Rays in a linear sound-speed gradient are circular arcs of this radius."""
    if gradient == 0:
        return float("inf")
    return NOMINAL_SOUND_SPEED / abs(gradient)


def arc_to_layer_m(profile, depth_m):
    """Horizontal distance from a platform to the shallowest vertex a refracted,
    boundary-free ray can reach.

    Above the sonic layer depth sound speed increases downward, so rays bend
    upward and the deepest a boundary-free ray turns is the layer itself. Below
    it sound speed decreases downward, so rays bend downward and the shallowest
    turn is again the layer. Either way the layer is the common vertex, and for a
    small-angle circular arc the horizontal reach is sqrt(2 * R * dz).
    """
    layer = profile.layer_depth_m
    if depth_m <= layer:
        radius = radius_of_curvature_m(profile.duct_gradient)
        separation = layer - depth_m
    else:
        radius = radius_of_curvature_m(profile.below_layer_gradient)
        separation = depth_m - layer
    if radius == float("inf"):
        return float("inf")
    return math.sqrt(2.0 * radius * max(0.0, separation))


def refracted_limiting_range_m(profile, depth_a_m, depth_b_m):
    """Longest horizontal range joined by a refracted ray that touches neither
    the surface nor the bottom. Beyond it the geometry is in shadow and only
    boundary-interacting or leaked energy arrives."""
    return arc_to_layer_m(profile, depth_a_m) + arc_to_layer_m(profile, depth_b_m)


@dataclass(frozen=True)
class PathResult:
    name: str
    status: str
    loss_db: float
    note: str

    def public_definition(self):
        return {"path": self.name, "status": self.status, "note": self.note}


@dataclass(frozen=True)
class PathSolution:
    """Every candidate path, and the combined loss over those that carry energy."""
    loss_db: float
    status: str
    paths: tuple

    @property
    def dominant(self):
        carrying = [path for path in self.paths if path.loss_db is not None]
        return min(carrying, key=lambda path: path.loss_db).name if carrying else None

    def public_definition(self):
        return {"combined_status": self.status,
                "paths": [path.public_definition() for path in self.paths]}


def _spherical(range_m, vertical_m, frequency_hz):
    slant = math.hypot(range_m, vertical_m)
    slant = max(slant, 1.0)
    return 20.0 * math.log10(slant) + absorption_db_per_metre(frequency_hz) * slant, slant


def _direct_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m, limit_m):
    if range_m > limit_m:
        return PathResult("direct", "out_of_scope", None,
                          f"Beyond the {metres_to_nautical_miles(limit_m):.1f} nm refracted limit for these depths.")
    loss, _ = _spherical(range_m, depth_b_m - depth_a_m, frequency_hz)
    return PathResult("direct", "supported", quantise(loss),
                      "Refracted path with no boundary interaction; spherical spreading and absorption.")


def _surface_duct_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m):
    profile = environment.profile
    if not profile.has_surface_duct:
        return PathResult("surface_duct", "out_of_scope", None, "No surface duct in this profile.")
    layer = profile.layer_depth_m
    if depth_a_m > layer or depth_b_m > layer:
        return PathResult("surface_duct", "out_of_scope", None,
                          f"Both platforms must be above the layer at "
                          f"{metres_to_feet(layer):.0f} feet to use the duct.")
    cutoff = profile.duct_cutoff_hz
    if frequency_hz < cutoff:
        return PathResult("surface_duct", "out_of_scope", None,
                          f"Below the {cutoff:.0f} Hz cutoff for a {metres_to_feet(layer):.0f} foot duct.")
    radius = radius_of_curvature_m(profile.duct_gradient)
    transition = math.sqrt(2.0 * radius * layer)
    leakage = DUCT_LEAKAGE_DB_PER_KM * math.sqrt(cutoff / frequency_hz) * range_m / 1000.0
    if range_m <= transition:
        loss = 20.0 * math.log10(max(range_m, 1.0))
    else:
        loss = 20.0 * math.log10(transition) + 10.0 * math.log10(range_m / transition)
    loss += absorption_db_per_metre(frequency_hz) * range_m + leakage
    status = "uncertain" if frequency_hz < cutoff * DUCT_CUTOFF_UNCERTAIN_RATIO else "supported"
    note = ("Trapped in the surface duct: spherical spreading to the transition range, then cylindrical, "
            "with a fitted leakage rate.")
    if status == "uncertain":
        note += " Frequency is close to cutoff, where trapping is partial."
    return PathResult("surface_duct", status, quantise(loss), note)


def _shadow_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m, limit_m):
    if range_m <= limit_m:
        return PathResult("shadow_zone", "out_of_scope", None, "A refracted path is available at this range.")
    plateau = SHADOW_PLATEAU_BASE_DB + SHADOW_PLATEAU_SLOPE_DB * math.log10(frequency_hz / 100.0)
    excess = plateau * (1.0 - math.exp(-(range_m - limit_m) / SHADOW_RAMP_METRES))
    loss, _ = _spherical(range_m, depth_b_m - depth_a_m, frequency_hz)
    return PathResult("shadow_zone", "uncertain", quantise(loss + excess),
                      "Energy scattered and leaked into the geometric shadow; a fitted excess loss, "
                      "not a diffraction solution.")


def _bottom_bounce_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m):
    water_depth = environment.water_depth_m
    if water_depth <= max(depth_a_m, depth_b_m) + 1.0:
        return PathResult("bottom_bounce", "out_of_scope", None, "Charted depth does not clear both platforms.")
    vertical = 2.0 * water_depth - depth_a_m - depth_b_m
    loss, slant = _spherical(range_m, vertical, frequency_hz)
    grazing = math.degrees(math.atan2(vertical, max(range_m, 1.0)))
    if grazing < BOTTOM_MINIMUM_GRAZING_DEG:
        return PathResult("bottom_bounce", "out_of_scope", None,
                          f"Grazing angle below {BOTTOM_MINIMUM_GRAZING_DEG:g} degrees; refraction governs the "
                          "geometry and the straight-ray approximation does not apply.")
    loss += bottom_loss_db(environment.bottom, grazing)
    status = "supported" if grazing >= BOTTOM_CONFIDENT_GRAZING_DEG else "uncertain"
    note = (f"Single bottom reflection at {grazing:.0f} degrees grazing over {environment.bottom}; "
            "straight-ray image source, so refraction of the slant path is not applied.")
    return PathResult("bottom_bounce", status, quantise(loss), note)


def _convergence_zone_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m):
    critical = environment.profile.critical_depth_m
    if critical is None:
        return PathResult("convergence_zone", "out_of_scope", None,
                          "Sound speed never returns to its near-surface maximum; there is no critical depth.")
    if environment.water_depth_m < critical + DEPTH_EXCESS_MARGIN_M:
        return PathResult("convergence_zone", "out_of_scope", None,
                          f"Charted depth {metres_to_feet(environment.water_depth_m):.0f} feet is above the "
                          f"{metres_to_feet(critical):.0f} foot critical depth; no depth excess, so no "
                          "convergence zone forms here.")
    order = round(range_m / CONVERGENCE_ZONE_SPACING_M)
    if order < 1:
        return PathResult("convergence_zone", "out_of_scope", None, "Inside the first convergence zone range.")
    centre = order * CONVERGENCE_ZONE_SPACING_M
    width = centre * CONVERGENCE_ZONE_WIDTH_FRACTION
    if abs(range_m - centre) > width / 2.0:
        return PathResult("convergence_zone", "out_of_scope", None,
                          f"Between convergence zones; the nearest annulus is centred at "
                          f"{metres_to_nautical_miles(centre):.0f} nm.")
    loss = (20.0 * math.log10(range_m) + absorption_db_per_metre(frequency_hz) * range_m
            - CONVERGENCE_ZONE_GAIN_DB)
    return PathResult("convergence_zone", "uncertain", quantise(loss),
                      f"Range-keyed annulus {order} with a fixed focusing gain; the caustic structure is not "
                      "computed.")


def transmission_loss(environment, frequency_hz, range_m, depth_a_m, depth_b_m):
    """Combine the candidate paths between two depths at one frequency.

    Reciprocal in the two depths by construction. Paths that carry energy are
    summed on an intensity basis; paths outside the model's scope contribute
    nothing but are still reported.
    """
    range_m = max(range_m, 1.0)
    if range_m > MAXIMUM_MODELLED_RANGE_M:
        note = (f"Beyond the {metres_to_nautical_miles(MAXIMUM_MODELLED_RANGE_M):.0f} nm limit of "
                "these approximations; the model does not answer at this range.")
        return PathSolution(None, "out_of_scope", tuple(
            PathResult(name, "out_of_scope", None, note) for name in
            ("direct", "surface_duct", "shadow_zone", "bottom_bounce", "convergence_zone")))
    limit = refracted_limiting_range_m(environment.profile, depth_a_m, depth_b_m)
    paths = (
        _direct_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m, limit),
        _surface_duct_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m),
        _shadow_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m, limit),
        _bottom_bounce_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m),
        _convergence_zone_path(environment, frequency_hz, range_m, depth_a_m, depth_b_m),
    )
    carrying = [path for path in paths if path.loss_db is not None]
    if not carrying:
        return PathSolution(None, "out_of_scope", paths)
    intensity = sum(10.0 ** (-path.loss_db / 10.0) for path in carrying)
    combined = quantise(-10.0 * math.log10(intensity))
    status = "supported" if any(path.status == "supported" for path in carrying) else "uncertain"
    return PathSolution(combined, status, paths)


def ambient_spectrum_level_db(environment, band):
    """Wenz-shaped ambient: distant shipping plus wind, combined on an intensity
    basis. Spectrum level, dB re 1 micropascal squared per hertz."""
    shipping = (SHIPPING_SPECTRUM_DB[band.identifier]
                + (environment.shipping_density - REFERENCE_SHIPPING_DENSITY) * SHIPPING_DENSITY_STEP_DB)
    wind = (WIND_SPECTRUM_DB[band.identifier]
            + (environment.sea_state - REFERENCE_SEA_STATE) * SEA_STATE_STEP_DB)
    return 10.0 * math.log10(10.0 ** (shipping / 10.0) + 10.0 ** (wind / 10.0))


def ambient_noise_db(environment, band, receiver_depth_m=None):
    """Ambient level in the band's analysis bandwidth.

    A receiver inside the surface duct hears the duct's trapped shipping noise as
    well, so its noise floor is higher than the same receiver below the layer.
    """
    level = (ambient_spectrum_level_db(environment, band)
             + 10.0 * math.log10(band.analysis_bandwidth_hz))
    if (receiver_depth_m is not None and environment.profile.has_surface_duct
            and receiver_depth_m <= environment.profile.layer_depth_m):
        level += DUCT_AMBIENT_EXCESS_DB[band.identifier]
    return quantise(level)


def combine_db(*levels):
    """Add incoherent levels on an intensity basis."""
    return quantise(10.0 * math.log10(sum(10.0 ** (level / 10.0) for level in levels)))


def flow_noise_rise_db(speed_knots, quiet_speed_knots):
    """Self-noise increase above a receiver's quiet speed.

    Zero at or below the quiet speed, then a fixed slope per decade of speed.
    This is what makes slowing down to listen a real choice rather than a
    narrative one.
    """
    if speed_knots <= quiet_speed_knots or quiet_speed_knots <= 0:
        return 0.0
    return quantise(SELF_NOISE_SPEED_SLOPE_DB * math.log10(speed_knots / quiet_speed_knots))


def radiated_speed_rise_db(speed_knots, reference_speed_knots):
    """Radiated noise above the speed a signature is quoted at."""
    if speed_knots <= 0 or reference_speed_knots <= 0:
        return 0.0
    return quantise(RADIATED_SPEED_SLOPE_DB
                    * math.log10(max(speed_knots, 0.5) / reference_speed_knots))


def cavitation_speed_knots(base_knots, per_hundred_feet, depth_feet):
    """Speed at which a propeller starts to cavitate at a given depth.

    Deeper water suppresses cavitation, so depth buys speed. A fictional linear
    rule standing in for the real pressure relation.
    """
    if base_knots is None:
        return None
    return quantise(base_knots + per_hundred_feet * depth_feet / 100.0, 2)


def detection_threshold_db(band, integration_seconds, detection_index_db):
    """Signal-to-noise ratio required at the array.

    The classic energy-detector relation: longer integration and a narrower
    analysis bandwidth both lower the threshold by 5 dB per decade.
    """
    return quantise(5.0 * math.log10(band.analysis_bandwidth_hz / integration_seconds)
                    + detection_index_db)


def signal_excess_db(source_level_db, loss_db, noise_db, array_gain_db, threshold_db,
                     target_strength_db=None):
    """Passive, or active when a target strength is supplied (two-way loss)."""
    if loss_db is None:
        return None
    if target_strength_db is None:
        return quantise(source_level_db - loss_db - noise_db + array_gain_db - threshold_db)
    return quantise(source_level_db - 2.0 * loss_db + target_strength_db
                    - noise_db + array_gain_db - threshold_db)


def detection_probability(excess_db):
    """Cumulative normal in signal excess: even odds at SE = 0.

    Quantised before it reaches a random-draw comparison so that replay does not
    depend on the last bit of the error function.
    """
    if excess_db is None:
        return 0.0
    probability = 0.5 * (1.0 + math.erf(excess_db / (SIGNAL_EXCESS_SIGMA_DB * math.sqrt(2.0))))
    bounded = min(MAXIMUM_DETECTION_PROBABILITY, max(MINIMUM_DETECTION_PROBABILITY, probability))
    return quantise(bounded, 6)


def public_band_catalogue():
    return [band.public_definition() for band in BANDS]
