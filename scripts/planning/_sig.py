"""Temporary: print middleware signatures."""
import inspect

from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

print(inspect.signature(ModelCallLimitMiddleware.__init__))
print(inspect.signature(ToolCallLimitMiddleware.__init__))
