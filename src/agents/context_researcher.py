from agents import Agent

from src.agents.model import get_model_config
from src.tools import grep_files, read_file_content, list_directory

CONTEXT_RESEARCHER_INSTRUCTIONS = """You research context from markdown files representing Slack channels, 
Google Drive documents, and other sources. Given an issue prompt, search through the 
provided directory to find all relevant context.

Strategy:
1. Start with broad keyword searches related to the issue
2. Read promising files to understand context
3. Search for related terms, people, and project names you discover
4. Return a comprehensive summary of all relevant context found

Be thorough - loop through multiple grep searches to find all relevant information."""

CONTEXT_RESEARCHER_TOOLS = [grep_files, read_file_content, list_directory]


def create_context_researcher(model_shorthand: str | None = None) -> Agent:
    """Create a context researcher agent with the specified model."""
    config = get_model_config(model_shorthand)
    return Agent(
        name="ContextResearcher",
        model=config.model,
        model_settings=config.model_settings,
        instructions=CONTEXT_RESEARCHER_INSTRUCTIONS,
        tools=CONTEXT_RESEARCHER_TOOLS,
    )


# Default instance for backwards compatibility
context_researcher = create_context_researcher()
