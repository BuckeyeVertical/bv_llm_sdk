"""Score a model on drone requests in the simulator. See
src/bv_llm_sdk/toolkits/drone/evals/runner.py for options, or run with --help."""

import sys

from bv_llm_sdk.toolkits.drone.evals.runner import main

if __name__ == "__main__":
    sys.exit(main())
