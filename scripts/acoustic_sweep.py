#!/usr/bin/env python3
"""Print detection probability against range for the published acoustic model.

A calibration and review aid, not part of the game. It builds a disposable world
from a fixed test seed, then evaluates the same sonar equation the engine uses
over a grid of ranges, depths and speeds. Nothing is saved and no live session is
touched.

    python scripts/acoustic_sweep.py
    python scripts/acoustic_sweep.py --own-depth 200 --speeds 5 10 15
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from submarine_command import acoustics, engine
from submarine_command.platforms import BIOLOGIC, DART, MERCHANT, WARSHIP

TEST_SEED = "5a" * 32
RANGES_NM = (1, 2, 3, 5, 8, 10, 15, 20, 25, 30)


def probe(state, spec, contact_depth_feet, own_depth_feet, speed_knots, range_nm,
          mode=None, contact_speed=None):
    """Score one geometry without mutating the disposable world."""
    own = dict(state["own"], x=0.0, y=0.0, depth=own_depth_feet, speed=speed_knots)
    selected = mode or spec.default_mode
    if contact_speed is None:
        # Quote each hull at the speed its own signature is referenced to, so the
        # table shows propagation rather than an arbitrary speed choice.
        contact_speed = spec.signature.reference_speed_knots
    contact_speed = min(max(contact_speed, spec.envelope.minimum_speed_knots),
                        spec.mode(selected).maximum_speed_knots)
    source = engine.make_entity(
        spec, id="probe", x=range_nm, y=0.0, course=90.0,
        speed=contact_speed, depth=contact_depth_feet)
    source["operating_mode"] = selected
    probe_state = dict(state, own=own, t=0)
    return engine.acoustic_ledger(probe_state, own, source)


def sweep(state, title, spec, contact_depth_feet, own_depths, speeds, mode=None):
    print(f"\n{title}")
    print(f"{'own ft':>7} {'kn':>4} " + " ".join(f"{nm:>5}" for nm in RANGES_NM))
    for own_depth in own_depths:
        for speed in speeds:
            cells = []
            for range_nm in RANGES_NM:
                ledger = probe(state, spec, contact_depth_feet, own_depth, speed, range_nm, mode)
                cells.append("  -  " if ledger is None else f"{ledger['probability'] * 100:5.0f}")
            print(f"{own_depth:>7.0f} {speed:>4.0f} " + " ".join(cells))
    print("     per five-minute step, percent")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--own-depths", type=float, nargs="+", default=[150, 400, 700])
    parser.add_argument("--speeds", type=float, nargs="+", default=[5, 12])
    arguments = parser.parse_args()
    state = engine.new_world(TEST_SEED)
    ocean = state["ocean"]
    model = engine.ocean_model(state)
    print("Disposable test world, seed is a published test value.")
    print(f"layer depth {acoustics.metres_to_feet(model.layer_depth_m(0, 0)):.0f} ft west to "
          f"{acoustics.metres_to_feet(model.layer_depth_m(24, 0)):.0f} ft east; "
          f"sea state {ocean['sea_state']}; bottom {ocean['bottom']}")
    print("Contacts are quoted at the speed their signature is referenced to.")
    sweep(state, "Diesel submarine on battery at 500 feet", DART, 500,
          arguments.own_depths, arguments.speeds, "battery")
    sweep(state, "Diesel submarine snorkeling at 50 feet", DART, 50,
          arguments.own_depths, arguments.speeds, "snorkeling")
    sweep(state, "Merchant on the surface", MERCHANT, 0,
          arguments.own_depths, arguments.speeds)
    sweep(state, "Patrol combatant on the surface", WARSHIP, 0,
          arguments.own_depths, arguments.speeds)
    sweep(state, "Vocalizing biologic group at 300 feet", BIOLOGIC, 300,
          arguments.own_depths, arguments.speeds)
    print("\nOwn ship as heard by a diesel submarine at 500 feet:")
    print(f"{'own ft':>7} {'kn':>4} " + " ".join(f"{nm:>5}" for nm in RANGES_NM))
    for own_depth in arguments.own_depths:
        for speed in arguments.speeds:
            cells = []
            for range_nm in RANGES_NM:
                own = dict(state["own"], x=0.0, y=0.0, depth=own_depth, speed=speed)
                listener = engine.make_entity(
                    DART, id="listener", x=range_nm, y=0.0, course=270.0, speed=4.0, depth=500)
                ledger = engine.acoustic_ledger(dict(state, own=own, t=0), listener, own)
                cells.append("  -  " if ledger is None else f"{ledger['probability'] * 100:5.0f}")
            print(f"{own_depth:>7.0f} {speed:>4.0f} " + " ".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
