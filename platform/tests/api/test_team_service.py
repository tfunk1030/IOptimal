from api.services.team_service import compare_sessions


def test_compare_sessions_builds_diff_sections():
    rows = [
        {
            "id": "a",
            "results": {
                "driver_style": "smooth",
                "lap_time_s": 110.1,
                "step1_rake": {"static_rear_rh_mm": 50.0},
                "step2_heave": {"front_heave_nmm": 30.0},
            },
        },
        {
            "id": "b",
            "results": {
                "driver_style": "aggressive",
                "lap_time_s": 109.7,
                "step1_rake": {"static_rear_rh_mm": 48.0},
                "step2_heave": {"front_heave_nmm": 40.0},
            },
        },
    ]
    payload = compare_sessions(rows)
    assert len(payload["setup_diff"]) >= 1
    assert len(payload["style_diff"]) == 1
    assert len(payload["performance_diff"]) == 1

