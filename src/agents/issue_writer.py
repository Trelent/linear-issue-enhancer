from agents import Agent

from src.agents.model import get_model

issue_writer = Agent(
    name="IssueWriter",
    model=get_model(),
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
