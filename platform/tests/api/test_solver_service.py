from api.services.solver_service import make_produce_args


def test_make_produce_args_sets_expected_flags():
    args = make_produce_args(
        car="bmw",
        ibt_path="sample.ibt",
        wing=17.0,
        fuel=89.0,
        lap=22,
        sto_path="out.sto",
        json_path="out.json",
        learn=True,
        auto_learn=True,
    )
    assert args.car == "bmw"
    assert args.ibt == "sample.ibt"
    assert args.wing == 17.0
    assert args.fuel == 89.0
    assert args.lap == 22
    assert args.sto == "out.sto"
    assert args.json == "out.json"
    assert args.no_learn is False
    assert args.min_lap_time == 108.0
    assert args.outlier_pct == 0.115

