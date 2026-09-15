"""Public, fictional platform specifications used by command validation.

Supported capabilities and planned fidelity are reported separately. These are
game parameters, not technical characteristics of a real submarine class.
"""
from dataclasses import asdict, dataclass
from enum import Enum


class Propulsion(str, Enum):
    NUCLEAR = "nuclear"


@dataclass(frozen=True)
class OperatingEnvelope:
    minimum_speed_knots: float
    maximum_speed_knots: float
    minimum_depth_feet: float
    maximum_depth_feet: float


@dataclass(frozen=True)
class MastEnvelope:
    minimum_depth_feet: float
    maximum_depth_feet: float
    maximum_speed_knots: float

    def allows(self, depth_feet, speed_knots):
        return self.minimum_depth_feet <= depth_feet <= self.maximum_depth_feet and 0 <= speed_knots <= self.maximum_speed_knots


@dataclass(frozen=True)
class PlatformSpec:
    identifier: str
    class_name: str
    propulsion: Propulsion
    envelope: OperatingEnvelope
    mast: MastEnvelope
    repair_maximum_speed_knots: float

    def public_capabilities(self):
        return {
            "platform_id": self.identifier,
            "class": self.class_name,
            "fictional": True,
            "propulsion": self.propulsion.value,
            "endurance": "No fuel, battery or snorkeling constraint during this short nuclear-submarine patrol; plant transients are not modeled.",
            "operating_envelope": asdict(self.envelope),
            "sonar": {
                "implemented": ["combined passive reception", "focused contact analysis", "one active pulse with an imperfect range measurement"],
                "towed_array_modeled": False,
                "separate_array_geometry_modeled": False,
                "spectral_frequencies_modeled": False,
            },
            "communications": {
                "transmit": "mast only", "receive": "mast only",
                "mast_envelope": asdict(self.mast),
                "submerged_reception_modeled": False,
                "buoyant_array_modeled": False,
            },
            "environment": {
                "implemented": "Uncertain layer depth and a simplified loss across the layer.",
                "full_sound_speed_profile_modeled": False,
                "convergence_zone_modeled": False,
                "bottom_bounce_modeled": False,
                "half_channel_modeled": False,
            },
            "weapons": {"loadout_modeled": False, "employment_modeled": False},
            "measurements": {
                "bearing_history": True,
                "bearing_drift": "Endpoint change from recorded bearings, labeled as an observation trend; not a target-motion solution.",
                "numeric_narrowband_frequencies": False,
            },
        }


KESTREL = PlatformSpec(
    identifier="kestrel-ssn-v1",
    class_name="Kestrel-class exercise SSN",
    propulsion=Propulsion.NUCLEAR,
    envelope=OperatingEnvelope(2, 18, 60, 900),
    mast=MastEnvelope(60, 80, 8),
    repair_maximum_speed_knots=10,
)
