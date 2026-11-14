"""Unit tests for GenAI foundational utilities."""

import pytest
from src.prompts.templates import PromptTemplate
from src.agents.tools import CalculatorTool

def test_prompt_template_formatting():
    tpl = PromptTemplate("Hello, {name}!")
    result = tpl.format(name="Developer")
    assert result == "Hello, Developer!"

def test_calculator_tool():
    calc = CalculatorTool()
    assert calc.execute("2 + 2") == "4"
    assert calc.execute("10 * 5") == "50"
    assert "Error" in calc.execute("import os")
