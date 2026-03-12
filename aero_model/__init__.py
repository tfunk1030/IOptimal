"""Aerodynamic response surface model for iRacing GTP/Hypercar cars.

Parse aero map xlsx files and build interpolated surfaces for:
- DF_balance(front_RH, rear_RH) at each wing angle
- L_D(front_RH, rear_RH) at each wing angle
"""

from aero_model.interpolator import AeroSurface, load_car_surfaces
