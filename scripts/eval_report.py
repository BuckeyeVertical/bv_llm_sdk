"""Summarise eval results across runs and models. See
src/bv_llm_sdk/agent/evals/report.py, or run with --help."""

import sys

from bv_llm_sdk.agent.evals.report import main

if __name__ == "__main__":
    sys.exit(main())
