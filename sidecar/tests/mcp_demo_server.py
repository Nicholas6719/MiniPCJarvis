"""Tiny demo MCP server used to test JARVIS's plugin system."""
from mcp.server import MCPServer

mcp = MCPServer(name="demo")


@mcp.tool()
def add_numbers(a: float, b: float) -> str:
    """Add two numbers and return the sum."""
    return str(a + b)


@mcp.tool()
def reverse_text(text: str) -> str:
    """Reverse a string."""
    return text[::-1]


if __name__ == "__main__":
    mcp.run(transport="stdio")
