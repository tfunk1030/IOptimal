import sys
from pathlib import Path

from track_model.profile import TrackProfile
from car_model.cars import BMW_M_HYBRID_V8
from solver.wheel_geometry_solver import WheelGeometrySolver

def main():
    track_path = Path("data/tracks/sebring_international_raceway_international.json")
    track = TrackProfile.load(track_path)
    
    solver = WheelGeometrySolver(BMW_M_HYBRID_V8, track)
    
    # K roll front = 877 + 1650 = 2527
    # K roll rear = 1454 + 712 = 2166
    k_roll = 4694.0
    
    sol = solver.solve(
        k_roll_total_nm_deg=k_roll,
        front_wheel_rate_nmm=33.6,
        rear_wheel_rate_nmm=61.2,
        fuel_load_l=89.0
    )
    
    print(sol.summary())

if __name__ == "__main__":
    main()
