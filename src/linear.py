"""Linear API client for reading and updating issues."""

import os
import httpx
from dataclasses import dataclass


LINEAR_API_URL = "https://api.linear.app/graphql"


@dataclass
class LinearComment:
    id: str
    body: str
    user_id: str
    user_name: str
    created_at: str


@dataclass
class LinearIssue:
    id: str
    identifier: str  # e.g., "ENG-123"
    title: str
    description: str | None
    team_id: str
    team_name: str
    state_name: str
    url: str


def _get_api_key() -> str:
    key = os.getenv("LINEAR_API_KEY")
    if not key:
        raise ValueError("LINEAR_API_KEY environment variable is not set")
    return key


def _graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against Linear API."""
    headers = {
        "Authorization": _get_api_key(),
        "Content-Type": "application/json",
    }
    response = httpx.post(
        LINEAR_API_URL,
        json={"query": query, "variables": variables or {}},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise Exception(f"Linear API error: {data['errors']}")
    return data["data"]


async def _graphql_async(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against Linear API (async)."""
    headers = {
        "Authorization": _get_api_key(),
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            LINEAR_API_URL,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise Exception(f"Linear API error: {data['errors']}")
        return data["data"]


async def get_issue(issue_id: str) -> LinearIssue:
    """Fetch an issue by ID."""
    query = """
    query GetIssue($id: String!) {
        issue(id: $id) {
            id
            identifier
            title
            description
            url
            team {
                id
                name
            }
            state {
                name
            }
        }
    }
    """
    data = await _graphql_async(query, {"id": issue_id})
    issue = data["issue"]
    return LinearIssue(
        id=issue["id"],
        identifier=issue["identifier"],
        title=issue["title"],
        description=issue.get("description"),
        team_id=issue["team"]["id"],
        team_name=issue["team"]["name"],
        state_name=issue["state"]["name"],
        url=issue["url"],
    )


async def update_issue_description(issue_id: str, description: str) -> bool:
    """Update an issue's description."""
    mutation = """
    mutation UpdateIssue($id: String!, $description: String!) {
        issueUpdate(id: $id, input: { description: $description }) {
            success
        }
    }
    """
    data = await _graphql_async(mutation, {"id": issue_id, "description": description})
    return data["issueUpdate"]["success"]


async def add_comment(issue_id: str, body: str, parent_id: str | None = None) -> bool:
    """Add a comment to an issue, optionally as a reply to another comment.
    
    Args:
        issue_id: The issue ID to comment on
        body: The comment body
        parent_id: Optional parent comment ID to reply to (creates a threaded reply)
    """
    if parent_id:
        mutation = """
        mutation AddCommentReply($issueId: String!, $body: String!, $parentId: String!) {
            commentCreate(input: { issueId: $issueId, body: $body, parentId: $parentId }) {
                success
            }
        }
        """
        data = await _graphql_async(mutation, {"issueId": issue_id, "body": body, "parentId": parent_id})
    else:
        mutation = """
        mutation AddComment($issueId: String!, $body: String!) {
            commentCreate(input: { issueId: $issueId, body: $body }) {
                success
            }
        }
        """
        data = await _graphql_async(mutation, {"issueId": issue_id, "body": body})
    return data["commentCreate"]["success"]


async def get_issue_comments(issue_id: str) -> list[LinearComment]:
    """Fetch all comments for an issue, ordered by creation time."""
    query = """
    query GetIssueComments($id: String!) {
        issue(id: $id) {
            comments {
                nodes {
                    id
                    body
                    createdAt
                    user {
                        id
                        displayName
                    }
                }
            }
        }
    }
    """
    data = await _graphql_async(query, {"id": issue_id})
    nodes = data["issue"]["comments"]["nodes"]
    comments = [
        LinearComment(
            id=node["id"],
            body=node["body"],
            user_id=node["user"]["id"] if node.get("user") else "",
            user_name=node["user"]["displayName"] if node.get("user") else "Unknown",
            created_at=node["createdAt"],
        )
        for node in nodes
    ]
    # Sort by created_at ascending (oldest first)
    return sorted(comments, key=lambda c: c.created_at)

