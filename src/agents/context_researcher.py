from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from src.tools import grep_files, read_file_content, list_directory

MODEL = LitellmModel(model="anthropic/claude-sonnet-4-20250514")

context_researcher = Agent(
    name="ContextResearcher",
    model=MODEL,
    instructions="""You research context from markdown files representing Slack channels, 
Google Drive documents, and other sources. Given an issue prompt, search through the 
provided directory to find all relevant context.

Strategy:
1. Start with broad keyword searches related to the issue
2. Read promising files to understand context
3. Search for related terms, people, and project names you discover
4. Return a comprehensive summary of all relevant context found

Be thorough - loop through multiple grep searches to find all relevant information.""",
    tools=[grep_files, read_file_content, list_directory],
)

