"""Entry point for python -m learner.

DEPRECATED: Use 'python -m ioptimal ingest' instead.
"""
import sys
import warnings
from learner.ingest import main

if __name__ == "__main__":
    warnings.warn(
        "DeprecationWarning: Use 'python -m ioptimal ingest' instead of 'python -m learner'",
        DeprecationWarning,
        stacklevel=2
    )
    print("⚠️  DEPRECATED: Use 'python -m ioptimal ingest' instead of 'python -m learner'", file=sys.stderr)
    print("", file=sys.stderr)
    main()
