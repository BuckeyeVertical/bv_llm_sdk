"""Simulate many users sending requests to one model server."""

import sys

from bv_llm_sdk.toolkits.drone.evals.load import main

if __name__ == "__main__":
    sys.exit(main())
