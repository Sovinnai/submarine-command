"""Public fictional entity specifications shared by validation and resolution.

The values are game parameters, not characteristics of real classes. Vessels
and biologics share geometry, motion, operating modes and acoustic signatures.
Only entities that actually carry equipment or consumable resources define
those components.
"""
from dataclasses import asdict, dataclass
from enum import Enum

from .acoustics import capability_environment


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
class ManeuverRates:
    """Bounded fictional rates for resolving an ordered maneuver over time.

    Depth rate is expressed per knot because a submarine changes depth with
    planes: at bare steerageway it can barely do so, and at high speed it can
    do so quickly. That coupling makes going shallow quickly also mean going
    fast, and therefore loud, without a separate rule.
    """
    turn_degrees_per_minute: float
    acceleration_knots_per_minute: float
    deceleration_knots_per_minute: float
    depth_feet_per_minute_per_knot: float

    def depth_rate(self, speed_knots):
        return self.depth_feet_per_minute_per_knot * abs(speed_knots)

    def speed_rate(self, current_knots, ordered_knots):
        return (
            self.acceleration_knots_per_minute
            if ordered_knots >= current_knots
            else self.deceleration_knots_per_minute
        )

    def public_definition(self):
        return {
            "turn_degrees_per_minute": self.turn_degrees_per_minute,
            "acceleration_knots_per_minute": self.acceleration_knots_per_minute,
            "deceleration_knots_per_minute": self.deceleration_knots_per_minute,
            "depth_feet_per_minute_per_knot": self.depth_feet_per_minute_per_knot,
            "depth_rate_note": (
                "Ordered depth change is resolved at "
                f"{self.depth_feet_per_minute_per_knot:g} feet per minute for each "
                "knot of actual speed. This is the rate of a deliberate ordered "
                "depth change in this game, not a maximum achievable rate."
            ),
        }


@dataclass(frozen=True)
class OperatingMode:
    identifier: str
    description: str
    relative_noise: float
    maximum_speed_knots: float
    depth_minimum_feet: float | None = None
    depth_maximum_feet: float | None = None

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
    sensors: tuple[Equipment, ...] = ()
    communications: tuple[Equipment, ...] = ()
    weapons: tuple[InventoryItem, ...] = ()
    countermeasures: tuple[InventoryItem, ...] = ()
    resources: tuple[ResourceModel, ...] = ()
    mast: MastEnvelope | None = None
    repair_maximum_speed_knots: float | None = None
    maneuver: ManeuverRates | None = None
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
            "operating_modes": [asdict(mode) for mode in self.modes],
            "maneuver_rates": (
                self.maneuver.public_definition() if self.maneuver else None
            ),
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
                        "combined passive reception",
                        "focused contact analysis",
                        "numeric narrowband frequency measurements",
                        "one active pulse with an imperfect range measurement",
                    ],
                    "inventory": [
                        item.public_definition() for item in self.sensors
                    ],
                    "towed_array_modeled": False,
                    "separate_array_geometry_modeled": False,
                    "spectral_frequencies_modeled": True,
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
                "environment": capability_environment(),
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
                        "Endpoint change from recorded bearings over at most 30 minutes. "
                        "It is an observation trend, reported separately from the "
                        "target-motion family."
                    ),
                    "target_motion": (
                        "Constant course and speed on a published grid, fit to recorded "
                        "bearings, timestamps, provenance and own-ship positions. "
                        "Ambiguous solutions are kept. Hidden contact motion is not an "
                        "input. A residual is not a confirmed maneuver."
                    ),
                    "frequency_change": (
                        "Crew indication from successive measured frequencies after "
                        "removing own-ship Doppler. A shift inside the role's sigma "
                        "gate is not called. Aspect does not change source level."
                    ),
                    "numeric_narrowband_frequencies": True,
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
            "quiet", "Reduced auxiliary operation; speed limited.", 0.72, 7
        ),
        OperatingMode(
            "standard", "Normal patrol plant and auxiliary state.", 1.0, 14
        ),
        OperatingMode(
            "high_power", "Higher propulsion noise at high speed.", 1.65, 18
        ),
    ),
    default_mode="standard",
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
    maneuver=ManeuverRates(
        turn_degrees_per_minute=60,
        acceleration_knots_per_minute=1,
        deceleration_knots_per_minute=2,
        depth_feet_per_minute_per_knot=5,
    ),
)

DART = EntitySpec(
    identifier="dart-diesel-aip-v1",
    class_name="Dart-class diesel-electric/AIP submarine",
    category=EntityCategory.DIESEL_SUBMARINE,
    contact_domain=ContactDomain.SUBMERGED,
    propulsion=Propulsion.DIESEL_ELECTRIC_AIP,
    envelope=OperatingEnvelope(1, 14, 40, 650),
    modes=(
        OperatingMode("battery", "Submerged battery propulsion.", 0.58, 14),
        OperatingMode("aip", "Low-rate submerged endurance mode.", 0.78, 5),
        OperatingMode(
            "snorkeling",
            "Diesel recharge with a raised snorkel and high acoustic exposure.",
            2.2,
            7,
            40,
            60,
        ),
    ),
    default_mode="battery",
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
        OperatingMode("economy", "Economical commercial transit.", 1.45, 11),
        OperatingMode("service", "Normal scheduled service speed.", 1.75, 16),
    ),
    default_mode="service",
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
        OperatingMode("quiet_patrol", "Reduced machinery patrol state.", 1.05, 10),
        OperatingMode("cruise", "Normal military transit state.", 1.55, 18),
        OperatingMode("dash", "High-output propulsion state.", 2.5, 24),
    ),
    default_mode="cruise",
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
        OperatingMode("transit", "Steady transit between grounds.", 1.35, 11),
        OperatingMode("fishing", "Irregular low-speed fishing operations.", 1.8, 4),
    ),
    default_mode="transit",
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
        OperatingMode("quiet_transit", "Sparse vocalization during transit.", 0.35, 6),
        OperatingMode("vocalizing", "Persistent pulsed vocalization.", 1.1, 5),
        OperatingMode("foraging", "Irregular clicks and broadband movement.", 0.85, 3),
    ),
    default_mode="vocalizing",
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
