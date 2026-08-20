"""Template agent with function tools.

Replace the example tools and instruction with your own.
The create_agent() factory is used by wrangler to inject different models and prompts.
"""

from google.adk.agents import Agent


# ---------------------------------------------------------------------------
# Your tools — replace these with your actual business logic
# ---------------------------------------------------------------------------


def lookup_item(item_id: str) -> dict:
    """Look up an item by its ID.

    Args:
        item_id: The unique identifier of the item.
    """
    return {
        "item_id": item_id,
        "name": f"Item {item_id}",
        "status": "available",
        "price": 29.99,
    }


def submit_order(item_id: str, quantity: int, customer_name: str) -> dict:
    """Submit an order for an item.

    Args:
        item_id: The item to order.
        quantity: Number of items.
        customer_name: Name of the customer placing the order.
    """
    return {
        "order_id": "ORD-001",
        "item_id": item_id,
        "quantity": quantity,
        "customer": customer_name,
        "status": "confirmed",
        "total": 29.99 * quantity,
    }


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

TOOLS = [lookup_item, submit_order]

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_INSTRUCTION = (
    "You are a helpful assistant. Use the available tools to answer user questions."
)


def create_agent(model: str = DEFAULT_MODEL, instruction: str = DEFAULT_INSTRUCTION) -> Agent:
    """Factory function for wrangler integration.

    Wrangler calls this with different model/instruction combinations
    during optimization. Keep this function signature unchanged.
    """
    from wrangler.core.config import resolve_model

    return Agent(
        model=resolve_model(model),
        name="my_agent",
        description="Template agent with function tools.",
        instruction=instruction,
        tools=TOOLS,
    )


root_agent = create_agent()
