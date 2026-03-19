@echo off
"C:\Users\VYRAL\AppData\Local\Programs\Python\Python313\python.exe" -m py_compile "C:\Users\VYRAL\IOptimal\car_model\cars.py"
if %errorlevel% neq 0 echo FAIL: cars.py
"C:\Users\VYRAL\AppData\Local\Programs\Python\Python313\python.exe" -m py_compile "C:\Users\VYRAL\IOptimal\car_model\setup_registry.py"
if %errorlevel% neq 0 echo FAIL: setup_registry.py
"C:\Users\VYRAL\AppData\Local\Programs\Python\Python313\python.exe" -m py_compile "C:\Users\VYRAL\IOptimal\solver\wheel_geometry_solver.py"
if %errorlevel% neq 0 echo FAIL: wheel_geometry_solver.py
echo SYNTAX CHECK COMPLETE
