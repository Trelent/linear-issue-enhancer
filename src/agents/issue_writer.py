from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

MODEL = LitellmModel(model="anthropic/claude-sonnet-4-20250514")

issue_writer = Agent(
    name="IssueWriter",
    model=MODEL,
    instructions="""You write comprehensive Linear issues based on research context.

Given:
- An issue prompt
- Context from Slack/GDrive/other sources  
- Codebase analysis

Write a well-structured issue with:
- Clear title
- Problem description with context from communications
- Technical details from code analysis
- Acceptance criteria
- Any relevant links or references found

Make it actionable and thorough.""",
)

