"""Check the API key and the connection to the model server."""

import sys

from bv_llm_sdk.agent import AgentConfig, ModelError, make_model
from bv_llm_sdk.agent.messages import UserMessage
from bv_llm_sdk.toolkits.arm.tools import ARM_TOOLS

PLACEHOLDER_KEY = "your-vllm-api-key"


def main() -> int:
    config = AgentConfig.from_env()
    if not config.api_key or config.api_key == PLACEHOLDER_KEY:
        print("No API key found. Put your key in the .env file.")
        return 1
    model = make_model(config)
    tools = ARM_TOOLS.schemas(model.schema_format, ["get_status"])
    try:
        model.complete("Reply with one word.", [UserMessage("Say ok.")], tools)
    except ModelError as exc:
        if "401" in str(exc):
            print("The server rejected your API key. Check the key in the .env file.")
        else:
            print(f"Could not reach the model server. {exc}")
        return 1
    print(f"Setup is working. Connected to {config.model}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
