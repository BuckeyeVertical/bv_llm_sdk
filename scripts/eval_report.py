"""Summarize eval results across runs and models."""

import sys

from bv_llm_sdk.toolkits.drone.evals.report import main

if __name__ == "__main__":
    sys.exit(main())
