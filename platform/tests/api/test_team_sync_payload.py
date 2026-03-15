from api.models.database import DatabaseGateway
from api.services.team_service import TeamKnowledgeService


def test_sync_payload_uses_team_fallback_for_sparse_driver_history():
    db = DatabaseGateway()
    service = TeamKnowledgeService(db)

    # One driver observation only -> should trigger fallback.
    db.insert(
        "observations",
        {
            "session_id": "s1",
            "driver_id": "d1",
            "team_id": "t1",
            "car": "bmw",
            "track": "Sebring International Raceway",
            "data": {},
            "driver_style": {},
            "diagnosis": {},
            "created_at": "2026-03-15T00:00:00+00:00",
        },
    )
    db.insert(
        "models",
        {
            "driver_id": None,
            "team_id": "t1",
            "car": "bmw",
            "track": "Sebring International Raceway",
            "model_type": "empirical",
            "data": {"corrections": {"roll_gradient_measured_mean": 1.2}},
            "session_count": 10,
            "updated_at": "2026-03-15T00:00:00+00:00",
        },
    )

    payload = service.get_sync_payload(team_id="t1", driver_id="d1", car="bmw", track="Sebring")
    assert payload["fallback_mode"] == "team"
    assert payload["driver_session_count"] == 1
    assert payload["learnings_snapshot"]["model_data"]["corrections"]["roll_gradient_measured_mean"] == 1.2

