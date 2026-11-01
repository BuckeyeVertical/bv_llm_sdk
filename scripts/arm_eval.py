"""Score the agent on arm requests with a fake arm."""

import sys

from bv_llm_sdk.toolkits.arm.evals import main

if __name__ == "__main__":
    sys.exit(main())
