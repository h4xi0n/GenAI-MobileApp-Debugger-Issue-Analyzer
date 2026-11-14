"""Tutorial 05: Agent reasoning loop and tool calling."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.agents.tools import CalculatorTool

def run_agent_tool_demo():
    calc = CalculatorTool()
    query = "Calculate: (15 * 4) + 120"
    expression = "(15 * 4) + 120"

    print(f"Agent received task: {query}")
    print(f"Agent selecting tool: {calc.name} ({calc.description})")
    result = calc.execute(expression)
    print(f"Tool execution result: {result}")
    print(f"Final Agent Answer: The calculated result is {result}.")

if __name__ == "__main__":
    run_agent_tool_demo()
