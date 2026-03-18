@echo off
set PATH=C:\Program Files\Git\cmd;C:\Program Files\Git\bin;C:\Program Files\Git\usr\bin;%PATH%
cd /d "C:\Users\VYRAL\IOptimal"
git add car_model/cars.py car_model/setup_registry.py solver/wheel_geometry_solver.py
git commit -m "fix: enforce iRacing GTP legal camber limits (front -2.9, rear -1.9)" -m "- GarageRanges defaults: front -5.0->-2.9, rear -4.0->-1.9" -m "- WheelGeometryModel defaults: front -5.0->-2.9, rear -4.0->-1.9" -m "- BMW setup_registry specs: front -5.0->-2.9, rear -4.0->-1.9" -m "- Ferrari overrides: rear -2.5->-1.9 (was exceeding GTP legal max)" -m "- Added front+rear camber clamping in both WheelGeometrySolver paths"
git push origin codextwo
echo DONE: %ERRORLEVEL%
