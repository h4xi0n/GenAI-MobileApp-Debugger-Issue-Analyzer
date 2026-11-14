"""Tool interface and sample tools for agentic reasoning loops."""

from typing import Callable, Any

class Tool:
    """Base class for tools that an agent can invoke."""

    def __init__(self, name: str, description: str, func: Callable[..., Any]):
        self.name = name
        self.description = description
        self.func = func

    def execute(self, *args, **kwargs) -> Any:
        return self.func(*args, **kwargs)


class CalculatorTool(Tool):
    """Safe math evaluator tool."""

    def __init__(self):
        super().__init__(
            name="calculator",
            description="Evaluates simple arithmetic expressions (e.g., '12 * 8').",
            func=self._calculate
        )

    @staticmethod
    def _calculate(expression: str) -> str:
        # Limited safe character set evaluation
        allowed_chars = set("0123456789+-*/(). %")
        if not set(expression).issubset(allowed_chars):
            return "Error: Unsupported expression characters."
        try:
            return str(eval(expression, {"__builtins__": None}, {}))
        except Exception as e:
            return f"Error: {str(e)}"
