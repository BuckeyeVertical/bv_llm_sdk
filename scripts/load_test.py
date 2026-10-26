"""Simulate many people using one model server at once. See
src/bv_llm_sdk/toolkits/drone/evals/load.py, or run with --help."""

import sys

from bv_llm_sdk.toolkits.drone.evals.load import main

if __name__ == "__main__":
    sys.exit(main())
