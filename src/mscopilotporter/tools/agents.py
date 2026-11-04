import logging
from typing import Optional
from mscopilotporter.agents.registry import list_agents as broker_list_agents
from mscopilotporter.agents.registry import dispatch_agent as broker_dispatch_agent

logger = logging.getLogger("mscopilotporter.tools_agents")

def list_agents() -> list[dict]:
    """List available CLI agent workers with availability and metadata."""
    return broker_list_agents()

def dispatch_agent(
    agent: str,
    task: str,
    workspace_id: str,
    purpose: str = "",
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    network_access: bool = False,
) -> dict:
    """Dispatch an authorized worker agent asynchronously."""
    return broker_dispatch_agent(
        agent=agent,
        task=task,
        workspace_id=workspace_id,
        purpose=purpose,
        model=model,
        reasoning_effort=reasoning_effort,
        network_access=network_access,
    )
