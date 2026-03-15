import argparse

def make_produce_args(car: str, ibt_path: str, wing: float = None,
                      fuel: float = None, lap: int = None,
                      sto_path: str = None, json_path: str = None,
                      learn: bool = True, auto_learn: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        car=car,
        ibt=ibt_path,
        wing=wing,
        fuel=fuel,
        lap=lap,
        sto=sto_path,
        json=json_path,
        learn=learn,
        auto_learn=auto_learn,
        no_learn=not learn,
        min_lap_time=108.0,
        outlier_pct=0.115,
        report_only=False,
    )
