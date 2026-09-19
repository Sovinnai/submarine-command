"""Public fictional entity specifications shared by validation and resolution.

The values are game parameters, not characteristics of real classes. Vessels
and biologics share geometry, motion, operating modes and acoustic signatures.
Only entities that actually carry equipment or consumable resources define
those components.

Acoustic quantities use the reference levels defined in ``acoustics``: source
levels in dB re 1 micropascal at 1 m within each band's analysis bandwidth,
array gain and target strength in dB. An operating mode shifts a hull's
signature; it does not carry a scale of its own.
"""
from dataclasses import asdict, dataclass
from enum import Enum

from . import acoustics


class EntityCategory(str, Enum):
    SSN = "nuclear_attack_submarine"
    DIESEL_SUBMARINE = "diesel_electric_submarine"
    MERCHANT = "merchant_ship"
    SURFACE_WARSHIP = "surface_warship"
    FISHING_VESSEL = "fishing_vessel"
    BIOLOGIC = "biologic_group"


class ContactDomain(str, Enum):
    SUBMERGED = "submerged"
    SURFACE = "surface"
    BIOLOGIC = "biologic"


class Propulsion(str, Enum):
    NUCLEAR = "nuclear"
    DIESEL_ELECTRIC_AIP = "diesel_electric_aip"
    COMMERCIAL_DIESEL = "commercial_diesel"
    MILITARY_DIESEL = "military_diesel"
    BIOLOGICAL = "biological_locomotion"


@dataclass(frozen=True)
class OperatingEnvelope:
    minimum_speed_knots: float
    maximum_speed_knots: float
    minimum_depth_feet: float
    maximum_depth_feet: float

    def allows(self, speed_knots, depth_feet):
        return (
            self.minimum_speed_knots <= speed_knots <= self.maximum_speed_knots
            and self.minimum_depth_feet <= depth_feet <= self.maximum_depth_feet
        )


@dataclass(frozen=True)
class MastEnvelope:
    minimum_depth_feet: float
    maximum_depth_feet: float
    maximum_speed_knots: float

    def allows(self, depth_feet, speed_knots):
        return (
            self.minimum_depth_feet <= depth_feet <= self.maximum_depth_feet
            and 0 <= speed_knots <= self.maximum_speed_knots
        )


@dataclass(frozen=True)
class OperatingMode:
    """One selectable machinery state.

    ``level_offset_db`` shifts the hull's whole signature; ``band_offsets_db``
    adds a per-band shape where a state changes what it radiates rather than how
    much, such as a diesel running on a snorkel.
    """
    identifier: str
    description: str
    level_offset_db: float
    maximum_speed_knots: float
    depth_minimum_feet: float | None = None
    depth_maximum_feet: float | None = None
    band_offsets_db: tuple = ()

    def public_definition(self):
        result = asdict(self)
        result["band_offsets_db"] = list(self.band_offsets_db)
        return result

    def allows(self, speed_knots, depth_feet):
        return (
            speed_knots <= self.maximum_speed_knots
            and (
                self.depth_minimum_feet is None
                or depth_feet >= self.depth_minimum_feet
            )
            and (
                self.depth_maximum_feet is None
                or depth_feet <= self.depth_maximum_feet
            )
        )


@dataclass(frozen=True)
class AcousticSignature:
    """Radiated source levels by band, dB re 1 micropascal at 1 m.

    One level per band in ``acoustics.BANDS``, quoted at ``reference_speed_knots``
    in that band's analysis bandwidth, so a level can be compared directly against
    a noise level. Radiated noise then rises with speed, and steps again once the
    propeller cavitates at a speed the hull's depth allows. These are fictional
    game parameters chosen to give the intended detection ranges, not measurements
    of any real class.
    """
    source_level_db: tuple
    reference_speed_knots: float
    cavitation_base_knots: float | None = None
    cavitation_per_hundred_feet: float = 0.0
    speed_dependent: bool = True

    def __post_init__(self):
        if len(self.source_level_db) != len(acoustics.BANDS):
            raise ValueError("A signature needs one source level per modelled band.")

    def cavitation_speed_knots(self, depth_feet):
        return acoustics.cavitation_speed_knots(
            self.cavitation_base_knots, self.cavitation_per_hundred_feet, depth_feet)

    def levels_for(self, mode, speed_knots=None, depth_feet=0.0):
        """Operating state, then speed, then cavitation."""
        shape = mode.band_offsets_db or (0.0,) * len(acoustics.BANDS)
        rise = 0.0
        if speed_knots is not None and self.speed_dependent:
            rise = acoustics.radiated_speed_rise_db(speed_knots, self.reference_speed_knots)
            inception = self.cavitation_speed_knots(depth_feet)
            if inception is not None and speed_knots > inception:
                rise += acoustics.CAVITATION_EXCESS_DB
        return tuple(
            acoustics.quantise(level + mode.level_offset_db + extra + rise, 2)
            for level, extra in zip(self.source_level_db, shape)
        )

    def public_definition(self):
        return {"bands": list(acoustics.BAND_IDS),
                "source_level_db_re_1upa_at_1m": list(self.source_level_db),
                "quoted_at_speed_knots": self.reference_speed_knots,
                "speed_slope_db_per_decade":
                    acoustics.RADIATED_SPEED_SLOPE_DB if self.speed_dependent else 0.0,
                "cavitation_inception": (
                    None if self.cavitation_base_knots is None else
                    {"base_knots": self.cavitation_base_knots,
                     "knots_per_hundred_feet": self.cavitation_per_hundred_feet,
                     "excess_db": acoustics.CAVITATION_EXCESS_DB}),
                "reference": "Band level in each band's analysis bandwidth; fictional game parameter."}


@dataclass(frozen=True)
class ReceiverSpec:
    """One idealized acoustic receiver.

    Array gain and self noise are given by band. Self noise is quoted at the
    published quiet speed and rises with speed through the flow-noise slope in
    ``acoustics``. Separate arrays, baffles and deployment geometry are not
    modelled; this is a single combined receiver.
    """
    identifier: str
    array_gain_db: tuple
    self_noise_db: tuple
    quiet_speed_knots: float
    detection_index_db: float
    directional: bool = False

    def __post_init__(self):
        if len(self.array_gain_db) != len(acoustics.BANDS) or len(self.self_noise_db) != len(acoustics.BANDS):
            raise ValueError("A receiver needs one array gain and one self-noise level per modelled band.")

    def public_definition(self):
        return {"id": self.identifier,
                "bands": list(acoustics.BAND_IDS),
                "array_gain_db": list(self.array_gain_db),
                "self_noise_db_at_quiet_speed": list(self.self_noise_db),
                "quiet_speed_knots": self.quiet_speed_knots,
                "detection_index_db": self.detection_index_db,
                "directional_coverage_modelled": self.directional}


@dataclass(frozen=True)
class ResourceModel:
    identifier: str
    unit: str
    capacity: float
    initial: float
    hotel_rate_per_hour: float
    speed_squared_rate_per_hour: float
    replenishment: tuple[tuple[str, float], ...] = ()

    def public_definition(self):
        return {
            "id": self.identifier,
            "unit": self.unit,
            "capacity": self.capacity,
            "initial": self.initial,
            "consumption_per_hour": {
                "hotel": self.hotel_rate_per_hour,
                "speed_squared_coefficient": self.speed_squared_rate_per_hour,
            },
            "replenishment_per_hour_by_mode": dict(self.replenishment),
        }


@dataclass(frozen=True)
class Equipment:
    identifier: str
    description: str
    initial_state: str
    implemented_effect: str | None

    def public_definition(self):
        return {
            "id": self.identifier,
            "description": self.description,
            "configured_state": self.initial_state,
            "implemented": self.implemented_effect is not None,
            "implemented_effect": self.implemented_effect,
        }


@dataclass(frozen=True)
class InventoryItem:
    identifier: str
    description: str
    quantity: int
    employment_implemented: bool = False

    def public_definition(self):
        return {
            "id": self.identifier,
            "description": self.description,
            "quantity": self.quantity,
            "employment_implemented": self.employment_implemented,
        }


@dataclass(frozen=True)
class EntitySpec:
    identifier: str
    class_name: str
    category: EntityCategory
    contact_domain: ContactDomain
    propulsion: Propulsion
    envelope: OperatingEnvelope
    modes: tuple[OperatingMode, ...]
    default_mode: str
    signature: AcousticSignature
    target_strength_db: float
    receiver: ReceiverSpec | None = None
    sensors: tuple[Equipment, ...] = ()
    communications: tuple[Equipment, ...] = ()
    weapons: tuple[InventoryItem, ...] = ()
    countermeasures: tuple[InventoryItem, ...] = ()
    resources: tuple[ResourceModel, ...] = ()
    mast: MastEnvelope | None = None
    repair_maximum_speed_knots: float | None = None
    profile_maximum_speed_knots: float | None = None
    behavior_model: str = "maintain_course"

    def mode(self, identifier):
        try:
            return next(mode for mode in self.modes if mode.identifier == identifier)
        except StopIteration as error:
            raise ValueError(
                f"{identifier!r} is not an operating mode for {self.class_name}."
            ) from error

    def initial_components(self):
        return {
            "operating_mode": self.default_mode,
            "resources": {item.identifier: item.initial for item in self.resources},
            "equipment": {
                item.identifier: item.initial_state
                for item in self.sensors + self.communications
            },
            "inventory": {
                item.identifier: item.quantity
                for item in self.weapons + self.countermeasures
            },
        }

    def public_definition(self):
        return {
            "platform_id": self.identifier,
            "class": self.class_name,
            "fictional": True,
            "category": self.category.value,
            "contact_domain": self.contact_domain.value,
            "propulsion": self.propulsion.value,
            "operating_envelope": asdict(self.envelope),
            "operating_modes": [mode.public_definition() for mode in self.modes],
            "acoustic_signature": self.signature.public_definition(),
            "target_strength_db": self.target_strength_db,
            "receiver": self.receiver.public_definition() if self.receiver else None,
            "resources": [item.public_definition() for item in self.resources],
            "sensors": [item.public_definition() for item in self.sensors],
            "communications_inventory": [
                item.public_definition() for item in self.communications
            ],
            "weapons_inventory": [
                item.public_definition() for item in self.weapons
            ],
            "countermeasure_inventory": [
                item.public_definition() for item in self.countermeasures
            ],
            "behavior_model": self.behavior_model,
        }

    def public_capabilities(self):
        result = self.public_definition()
        result.update(
            {
                "endurance": (
                    "No fuel, battery or snorkeling constraint during this short "
                    "nuclear-submarine patrol; reactor transients are not modeled."
                ),
                "sonar": {
                    "implemented": [
                        "one combined passive receiver resolved through the sonar equation",
                        "per-band signal excess against ambient and own self noise",
                        "focused contact analysis",
                        "one active pulse with two-way loss, target strength and an imperfect range measurement",
                    ],
                    "inventory": [
                        item.public_definition() for item in self.sensors
                    ],
                    "receiver": self.receiver.public_definition() if self.receiver else None,
                    "bands": acoustics.public_band_catalogue(),
                    "detection_rule": (
                        "SE = SL - TL - (NL - DI) - DT per band; detection probability is a cumulative "
                        f"normal in signal excess with a {acoustics.SIGNAL_EXCESS_SIGMA_DB:g} dB spread."
                    ),
                    "towed_array_modeled": False,
                    "separate_array_geometry_modeled": False,
                    "beam_pattern_modeled": False,
                    "baffles_modeled": False,
                    "spectral_frequencies_modeled": False,
                    "narrowband_line_tracking_modeled": False,
                },
                "communications": {
                    "transmit": "mast only",
                    "receive": "mast only",
                    "mast_envelope": asdict(self.mast) if self.mast else None,
                    "inventory": [
                        item.public_definition() for item in self.communications
                    ],
                    "submerged_reception_modeled": False,
                    "buoyant_array_modeled": False,
                },
                "environment": {
                    "implemented": (
                        "A piecewise-linear sound speed profile, charted water depth, a bottom class and "
                        "sea state drive frequency-dependent transmission loss along named paths. The boat "
                        "adjudicates against true conditions and predicts from its own aged profile estimate."
                    ),
                    "sound_speed_profile_modeled": "Four-segment piecewise-linear control points; not a measured cast.",
                    "ray_solver_modeled": False,
                    "range_dependent_profile_modeled": (
                        "No. Conditions are sampled at the path midpoint and held constant along it."
                    ),
                    "paths": {
                        "direct": "Refracted, boundary-free, bounded by a ray-curvature limiting range.",
                        "surface_duct": "Trapped above the layer with a cutoff frequency and a fitted leakage rate.",
                        "shadow_zone": "Fitted excess loss beyond the refracted limit; not a diffraction solution.",
                        "bottom_bounce": "Single reflection by straight-ray image source with an angle-dependent loss table.",
                        "convergence_zone": "Gated on depth excess; a range-keyed annulus with a fixed gain, no caustic structure.",
                    },
                    "path_status_reported": ["supported", "uncertain", "out_of_scope"],
                    "half_channel_modeled": (
                        "Only as the surface duct case; a full water-column half channel is not separately modelled."
                    ),
                    "absorption": "Thorp's expression; published for roughly 100 Hz upward.",
                    "ambient_noise": "Wenz-shaped shipping and wind tables by band, sea state and shipping density.",
                    "internal_waves_modeled": False,
                    "surface_scattering_modeled": False,
                    "reverberation_modeled": False,
                },
                "weapons": {
                    "loadout_modeled": True,
                    "employment_modeled": False,
                    "exercise_restriction": (
                        "Observation-only patrol; weapon and countermeasure "
                        "employment is unavailable."
                    ),
                    "inventory": [
                        item.public_definition() for item in self.weapons
                    ],
                    "countermeasures": [
                        item.public_definition() for item in self.countermeasures
                    ],
                },
                "measurements": {
                    "bearing_history": True,
                    "bearing_drift": (
                        "Endpoint change from recorded bearings, labeled as an "
                        "observation trend; not a target-motion solution."
                    ),
                    "numeric_narrowband_frequencies": False,
                },
            }
        )
        return result


KESTREL = EntitySpec(
    identifier="kestrel-ssn-v2",
    class_name="Kestrel-class exercise SSN",
    category=EntityCategory.SSN,
    contact_domain=ContactDomain.SUBMERGED,
    propulsion=Propulsion.NUCLEAR,
    envelope=OperatingEnvelope(2, 18, 60, 900),
    modes=(
        OperatingMode(
            "quiet", "Reduced auxiliary operation; speed limited.", -5.0, 7
        ),
        OperatingMode(
            "standard", "Normal patrol plant and auxiliary state.", 0.0, 14
        ),
        OperatingMode(
            "high_power", "Higher propulsion noise at high speed.", 8.0, 18
        ),
    ),
    default_mode="standard",
    signature=AcousticSignature((124.0, 122.0, 117.0, 111.0, 104.0, 96.0),
                                reference_speed_knots=5.0,
                                cavitation_base_knots=6.0,
                                cavitation_per_hundred_feet=2.2),
    target_strength_db=12.0,
    receiver=ReceiverSpec(
        "integrated_passive",
        array_gain_db=(18.0, 20.0, 22.0, 23.0, 23.0, 20.0),
        self_noise_db=(68.0, 64.0, 58.0, 52.0, 46.0, 40.0),
        quiet_speed_knots=5.0,
        detection_index_db=8.0,
    ),
    sensors=(
        Equipment(
            "integrated_passive",
            "Combined hull and flank reception used by the current acoustic model.",
            "available",
            "passive and focused acoustic observations",
        ),
        Equipment(
            "active_projector",
            "Idealized active projector.",
            "available",
            "one active pulse and imperfect range observation",
        ),
        Equipment(
            "towed_array",
            "Idealized deployable array reserved for the separate-array rules.",
            "stowed",
            None,
        ),
    ),
    communications=(
        Equipment(
            "radio_mast",
            "Retractable mast radio.",
            "stowed",
            "five-minute receive or transmit link cycle",
        ),
        Equipment(
            "buoyant_receive_array",
            "Receive-only buoyant array reserved for later communications rules.",
            "stowed",
            None,
        ),
    ),
    weapons=(
        InventoryItem(
            "exercise_heavyweight", "Fictional exercise heavyweight round.", 10
        ),
    ),
    countermeasures=(
        InventoryItem("mobile_decoy", "Fictional mobile acoustic decoy.", 6),
        InventoryItem("noise_maker", "Fictional expendable noise maker.", 12),
    ),
    mast=MastEnvelope(60, 80, 8),
    repair_maximum_speed_knots=10,
    profile_maximum_speed_knots=12,
)

DART = EntitySpec(
    identifier="dart-diesel-aip-v1",
    class_name="Dart-class diesel-electric/AIP submarine",
    category=EntityCategory.DIESEL_SUBMARINE,
    contact_domain=ContactDomain.SUBMERGED,
    propulsion=Propulsion.DIESEL_ELECTRIC_AIP,
    envelope=OperatingEnvelope(1, 14, 40, 650),
    modes=(
        OperatingMode("battery", "Submerged battery propulsion.", -4.0, 14),
        OperatingMode("aip", "Low-rate submerged endurance mode.", -2.0, 5),
        OperatingMode(
            "snorkeling",
            "Diesel recharge with a raised snorkel and high acoustic exposure.",
            8.0,
            7,
            40,
            60,
            # Running diesels add low-frequency lines on top of the overall rise.
            band_offsets_db=(4.0, 5.0, 2.0, 0.0, 0.0, 0.0),
        ),
    ),
    default_mode="battery",
    signature=AcousticSignature((141.0, 139.0, 133.0, 126.0, 118.0, 109.0),
                                reference_speed_knots=4.0,
                                cavitation_base_knots=5.0,
                                cavitation_per_hundred_feet=2.0),
    target_strength_db=10.0,
    receiver=ReceiverSpec(
        "integrated_passive",
        array_gain_db=(15.0, 17.0, 19.0, 20.0, 20.0, 18.0),
        self_noise_db=(70.0, 66.0, 60.0, 54.0, 48.0, 42.0),
        quiet_speed_knots=4.0,
        detection_index_db=9.0,
    ),
    sensors=(
        Equipment(
            "integrated_passive",
            "Combined passive receiver used by the current acoustic model.",
            "available",
            "opponent acoustic awareness",
        ),
        Equipment("towed_array", "Idealized compact towed receiver.", "stowed", None),
    ),
    communications=(
        Equipment("radio_mast", "Retractable mast radio.", "stowed", None),
    ),
    weapons=(
        InventoryItem("diesel_heavyweight", "Fictional heavyweight round.", 8),
    ),
    countermeasures=(
        InventoryItem("compact_decoy", "Fictional compact acoustic decoy.", 6),
    ),
    resources=(
        ResourceModel(
            "battery_energy",
            "energy units",
            100,
            72,
            1.2,
            0.055,
            (("aip", 2.0), ("snorkeling", 22.0)),
        ),
    ),
    mast=MastEnvelope(40, 60, 7),
    profile_maximum_speed_knots=10,
    behavior_model="energy_guarded_transit",
)

MERCHANT = EntitySpec(
    identifier="alder-merchant-v1",
    class_name="Alder commercial motor vessel",
    category=EntityCategory.MERCHANT,
    contact_domain=ContactDomain.SURFACE,
    propulsion=Propulsion.COMMERCIAL_DIESEL,
    envelope=OperatingEnvelope(3, 16, 0, 0),
    modes=(
        OperatingMode("economy", "Economical commercial transit.", -2.0, 11),
        OperatingMode("service", "Normal scheduled service speed.", 0.0, 16),
    ),
    default_mode="service",
    signature=AcousticSignature((138.0, 136.0, 131.0, 124.0, 116.0, 106.0),
                                reference_speed_knots=10.0),
    target_strength_db=22.0,
    sensors=(
        Equipment("navigation_radar", "Commercial navigation radar.", "radiating", None),
    ),
    communications=(
        Equipment("commercial_radio", "Commercial voice/data radio.", "available", None),
    ),
    resources=(
        ResourceModel("fuel", "fuel units", 1000, 820, 0.3, 0.012),
    ),
)

WARSHIP = EntitySpec(
    identifier="warden-surface-combatant-v1",
    class_name="Warden patrol combatant",
    category=EntityCategory.SURFACE_WARSHIP,
    contact_domain=ContactDomain.SURFACE,
    propulsion=Propulsion.MILITARY_DIESEL,
    envelope=OperatingEnvelope(3, 24, 0, 0),
    modes=(
        OperatingMode("quiet_patrol", "Reduced machinery patrol state.", -4.0, 10),
        OperatingMode("cruise", "Normal military transit state.", 0.0, 18),
        OperatingMode("dash", "High-output propulsion state.", 9.0, 24),
    ),
    default_mode="cruise",
    signature=AcousticSignature((140.0, 138.0, 133.0, 127.0, 120.0, 111.0),
                                reference_speed_knots=14.0),
    target_strength_db=18.0,
    receiver=ReceiverSpec(
        "hull_sonar",
        array_gain_db=(10.0, 12.0, 15.0, 17.0, 18.0, 17.0),
        self_noise_db=(82.0, 78.0, 72.0, 66.0, 60.0, 54.0),
        quiet_speed_knots=8.0,
        detection_index_db=9.0,
    ),
    sensors=(
        Equipment("surface_radar", "Air/surface search radar.", "silent", None),
        Equipment("hull_sonar", "Idealized hull sonar.", "passive", None),
    ),
    communications=(
        Equipment("military_radio", "Military line-of-sight radio.", "available", None),
    ),
    weapons=(
        InventoryItem("lightweight_round", "Fictional lightweight acoustic round.", 8),
        InventoryItem("guided_round", "Fictional guided surface round.", 4),
    ),
    countermeasures=(
        InventoryItem("surface_decoy", "Fictional surface acoustic decoy.", 8),
    ),
    resources=(
        ResourceModel("fuel", "fuel units", 700, 610, 0.5, 0.018),
    ),
)

FISHER = EntitySpec(
    identifier="tern-fishing-vessel-v1",
    class_name="Tern fishing vessel",
    category=EntityCategory.FISHING_VESSEL,
    contact_domain=ContactDomain.SURFACE,
    propulsion=Propulsion.COMMERCIAL_DIESEL,
    envelope=OperatingEnvelope(1, 11, 0, 0),
    modes=(
        OperatingMode("transit", "Steady transit between grounds.", 0.0, 11),
        OperatingMode(
            "fishing",
            "Irregular low-speed fishing operations.",
            3.0,
            4,
            # Gear handling is broadband and weighted to the higher bands.
            band_offsets_db=(-2.0, -1.0, 0.0, 2.0, 4.0, 4.0),
        ),
    ),
    default_mode="transit",
    signature=AcousticSignature((132.0, 130.0, 126.0, 121.0, 115.0, 108.0),
                                reference_speed_knots=8.0),
    target_strength_db=14.0,
    sensors=(
        Equipment("navigation_radar", "Small navigation radar.", "radiating", None),
    ),
    communications=(
        Equipment("commercial_radio", "Commercial voice radio.", "available", None),
    ),
    resources=(
        ResourceModel("fuel", "fuel units", 180, 142, 0.2, 0.02),
    ),
    behavior_model="fishing_cycle",
)

BIOLOGIC = EntitySpec(
    identifier="pelagic-biologic-group-v1",
    class_name="Pelagic vocalizing biologic group",
    category=EntityCategory.BIOLOGIC,
    contact_domain=ContactDomain.BIOLOGIC,
    propulsion=Propulsion.BIOLOGICAL,
    envelope=OperatingEnvelope(0.5, 6, 20, 1800),
    modes=(
        OperatingMode("quiet_transit", "Sparse vocalization during transit.", -10.0, 6),
        OperatingMode("vocalizing", "Persistent pulsed vocalization.", 0.0, 5),
        OperatingMode(
            "foraging",
            "Irregular clicks and broadband movement.",
            -3.0,
            3,
            # Foraging clicks concentrate what is radiated at the top of the band set.
            band_offsets_db=(-4.0, -3.0, -1.0, 2.0, 4.0, 6.0),
        ),
    ),
    default_mode="vocalizing",
    # Vocalizing biologics radiate high, not low: the shape, not just the level,
    # separates them from machinery.
    # Vocalization is not driven by swimming speed.
    signature=AcousticSignature((108.0, 114.0, 122.0, 128.0, 134.0, 137.0),
                                reference_speed_knots=3.0, speed_dependent=False),
    target_strength_db=-3.0,
    behavior_model="depth_and_vocalization_cycle",
)

ENTITY_SPECS = (KESTREL, DART, MERCHANT, WARSHIP, FISHER, BIOLOGIC)
ENTITY_REGISTRY = {spec.identifier: spec for spec in ENTITY_SPECS}


def get_spec(identifier):
    try:
        return ENTITY_REGISTRY[identifier]
    except KeyError as error:
        raise ValueError(f"Unknown entity specification {identifier!r}.") from error


def public_entity_catalog():
    """Return public rules definitions, not the hidden scenario selection."""
    return [spec.public_definition() for spec in ENTITY_SPECS]
