"""Allow running as: python -m pipeline ...

DEPRECATED: Use 'python -m ioptimal produce' instead.
"""

import sys
import warnings

from pipeline.produce import main

if __name__ == "__main__":
    warnings.warn(
        "DeprecationWarning: Use 'python -m ioptimal produce' instead of 'python -m pipeline'",
        DeprecationWarning,
        stacklevel=2
    )
    print("⚠️  DEPRECATED: Use 'python -m ioptimal produce' instead of 'python -m pipeline'", file=sys.stderr)
    print("", file=sys.stderr)
    main()
