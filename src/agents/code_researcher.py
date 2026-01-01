from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from src.tools import grep_files, read_file_content, list_directory, clone_repo

MODEL = LitellmModel(model="anthropic/claude-sonnet-4-20250514")

code_researcher = Agent(
    name="CodeResearcher",
    model=MODEL,
    instructions="""You analyze GitHub repositories to understand their structure and 
find code relevant to an issue.

Strategy:
1. First check README.md or agents.md for project overview
2. List directories to understand project structure
3. Search for code related to the issue using grep
4. Read relevant files to understand implementation details
5. Return a summary of the codebase and relevant code sections

Focus on understanding what the code does and finding sections relevant to the issue.""",
    tools=[grep_files, read_file_content, list_directory, clone_repo],
)

