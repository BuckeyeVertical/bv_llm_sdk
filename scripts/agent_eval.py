"""Score a model on drone requests in the simulator."""

import sys

from bv_llm_sdk.toolkits.drone.evals.runner import main

if __name__ == "__main__":
    sys.exit(main())
