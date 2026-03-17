import sys
import numpy as np
from track_model.ibt_parser import IBTFile
from car_model import get_car

def analyze_telemetry():
    ibt = IBTFile("ibtfiles/bmwtry.ibt")
    car = get_car("bmw")
    start, end = ibt.best_lap_indices()
    
    # Basic arrays
    vx = ibt.channel("VelocityX")[start:end+1]
    vy = ibt.channel("VelocityY")[start:end+1]
    speed = ibt.channel("Speed")[start:end+1]
    lat_g = ibt.channel("LatAccel")[start:end+1] / 9.81
    long_g = ibt.channel("LongAccel")[start:end+1] / 9.81
    vert_g = ibt.channel("VertAccel")[start:end+1] / 9.81
    yaw_rate = ibt.channel("YawRate")[start:end+1]
    steer = ibt.channel("SteeringWheelAngle")[start:end+1]
    
    # Suspensions
    hf_defl = ibt.channel("HFshockDefl")[start:end+1] * 1000 if ibt.has_channel("HFshockDefl") else np.zeros_like(speed)
    hr_defl = ibt.channel("HRshockDefl")[start:end+1] * 1000 if ibt.has_channel("HRshockDefl") else np.zeros_like(speed)
    lf_defl = ibt.channel("LFshockDefl")[start:end+1] * 1000 if ibt.has_channel("LFshockDefl") else np.zeros_like(speed)
    rf_defl = ibt.channel("RFshockDefl")[start:end+1] * 1000 if ibt.has_channel("RFshockDefl") else np.zeros_like(speed)
    lr_defl = ibt.channel("LRshockDefl")[start:end+1] * 1000 if ibt.has_channel("LRshockDefl") else np.zeros_like(speed)
    rr_defl = ibt.channel("RRshockDefl")[start:end+1] * 1000 if ibt.has_channel("RRshockDefl") else np.zeros_like(speed)
    
    # Ride heights
    lf_rh = ibt.channel("LFrideHeight")[start:end+1] * 1000
    rf_rh = ibt.channel("RFrideHeight")[start:end+1] * 1000
    lr_rh = ibt.channel("LRrideHeight")[start:end+1] * 1000
    rr_rh = ibt.channel("RRrideHeight")[start:end+1] * 1000
    
    # Aero Downforce Calculations
    # Get rates
    k_heave_f = car.front_heave_spring_nmm
    k_heave_r = car.rear_third_spring_nmm
    k_corner_f = car.corner_spring.torsion_bar_rate(13.9) # Approx
    k_corner_r = 170.0 * (0.6 ** 2) # MR=0.6 approx for rear
    
    # Dynamic forces (N)
    f_f_corner = (lf_defl + rf_defl) * k_corner_f
    f_f_heave = hf_defl * k_heave_f
    front_dyn_force = f_f_corner + f_f_heave
    
    f_r_corner = (lr_defl + rr_defl) * k_corner_r
    f_r_heave = hr_defl * k_heave_r
    rear_dyn_force = f_r_corner + f_r_heave
    
    total_dyn_force = front_dyn_force + rear_dyn_force
    
    # Convert speeds
    v_sq = speed ** 2
    
    # Mask for high speed straight line (no braking, high speed, low lat G)
    brake = ibt.channel("Brake")[start:end+1]
    straight_mask = (speed > 55.0) & (brake < 0.05) & (np.abs(lat_g) < 0.1)
    
    if np.sum(straight_mask) > 10:
        # Downforce coefficient Cl*A
        cla_front = front_dyn_force[straight_mask] / (0.5 * 1.225 * v_sq[straight_mask])
        cla_rear = rear_dyn_force[straight_mask] / (0.5 * 1.225 * v_sq[straight_mask])
        cla_total = total_dyn_force[straight_mask] / (0.5 * 1.225 * v_sq[straight_mask])
        
        print("--- AERO EFFICIENCY & DOWNFORCE ---")
        print(f"Calculated Cl*A Total: {np.mean(cla_total):.3f} (std {np.std(cla_total):.3f})")
        print(f"Calculated Cl*A Front: {np.mean(cla_front):.3f}")
        print(f"Calculated Cl*A Rear: {np.mean(cla_rear):.3f}")
        print(f"Aero Balance (Front %): {np.mean(cla_front / cla_total * 100):.1f}%")
        print(f"Max dynamic vertical force: {np.max(total_dyn_force):.0f} N")

    # Rake
    front_rh_avg = (lf_rh + rf_rh) / 2.0
    rear_rh_avg = (lr_rh + rr_rh) / 2.0
    rake = rear_rh_avg - front_rh_avg
    
    print("\n--- RAKE & PLATFORM ---")
    print(f"Mean Rake at speed (>200kph): {np.mean(rake[speed > 55.0]):.1f} mm")
    print(f"Min Front RH at speed: {np.min(front_rh_avg[speed > 55.0]):.1f} mm")
    
    # Slip Angles
    # wb = l_f + l_r. Weight distribution gives l_f and l_r
    l_f = car.wheelbase_m * (1.0 - car.weight_dist_front)
    l_r = car.wheelbase_m * car.weight_dist_front
    
    # Prevent div by zero
    vx_safe = np.maximum(vx, 5.0)
    
    # beta = Vy / Vx. slip_angle_r = beta - (yaw_rate * l_r / Vx)
    # slip_angle_f = delta - beta - (yaw_rate * l_f / Vx)
    road_wheel_angle = steer / car.steering_ratio
    
    rear_slip_rad = np.arctan2(vy - yaw_rate * l_r, vx_safe)
    front_slip_rad = road_wheel_angle - np.arctan2(vy + yaw_rate * l_f, vx_safe)
    
    rear_slip_deg = np.degrees(rear_slip_rad)
    front_slip_deg = np.degrees(front_slip_rad)
    
    cornering = (speed > 15.0) & (np.abs(lat_g) > 0.5)
    
    if np.sum(cornering) > 10:
        print("\n--- TIRE SLIP DYNAMICS ---")
        print(f"Front Slip Angle p95: {np.percentile(np.abs(front_slip_deg[cornering]), 95):.2f} deg")
        print(f"Rear Slip Angle p95: {np.percentile(np.abs(rear_slip_deg[cornering]), 95):.2f} deg")
        balance_slip = np.abs(front_slip_deg) - np.abs(rear_slip_deg)
        print(f"Slip Balance (Front - Rear) mean: {np.mean(balance_slip[cornering]):.2f} deg (>0 understeer, <0 oversteer)")

    # Brake Migration & Actual Brake Torque
    print("\n--- BRAKE DYNAMICS ---")
    if ibt.has_channel("BrakeBiasMigration"):
        migration = ibt.channel("BrakeBiasMigration")[start]
        print(f"Brake Migration Setting: {migration}")
    
    lf_press = ibt.channel("LFbrakeLinePress")[start:end+1]
    lr_press = ibt.channel("LRbrakeLinePress")[start:end+1]
    braking = brake > 0.3
    if np.sum(braking) > 10:
        actual_bias = np.mean((lf_press[braking]) / (lf_press[braking] + lr_press[braking] + 1e-5)) * 100
        print(f"Actual Mean Hydraulic Brake Bias under heavy braking: {actual_bias:.1f}%")
        
    print("\n--- AERO STALL DETECTION (VORTEX BURST) ---")
    # Stall happens if front RH drops too low and Cl*A front drops off a cliff.
    # Let's find moments where front_rh < 15mm and check cla_front
    low_rh = (front_rh_avg < 15.0) & straight_mask
    if np.sum(low_rh) > 5:
        cla_front_low = front_dyn_force[low_rh] / (0.5 * 1.225 * v_sq[low_rh])
        normal_rh = (front_rh_avg > 18.0) & straight_mask
        cla_front_normal = front_dyn_force[normal_rh] / (0.5 * 1.225 * v_sq[normal_rh]) if np.sum(normal_rh) > 5 else [0]
        print(f"Cl*A Front when RH < 15mm: {np.mean(cla_front_low):.3f}")
        print(f"Cl*A Front when RH > 18mm: {np.mean(cla_front_normal):.3f}")
        if np.mean(cla_front_low) < np.mean(cla_front_normal) * 0.95:
            print(">>> AERO STALL DETECTED at low ride heights!")
        else:
            print("No significant aero stall detected at low ride heights.")
    else:
        print("Not enough samples with front RH < 15mm on straights to detect stall.")
        
if __name__ == "__main__":
    analyze_telemetry()
