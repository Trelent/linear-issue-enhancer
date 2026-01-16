"""Tests for slash command system."""

import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# Ensure env vars are set before importing
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("LINEAR_API_KEY", "test-key")


class TestCommandRegistry:
    """Tests for the command registry."""

    def test_get_all_commands_returns_list(self):
        from src.commands.registry import get_all_commands
        
        commands = get_all_commands()
        
        assert isinstance(commands, list)
        assert len(commands) >= 3  # help, ask, retry

    def test_all_commands_have_required_attributes(self):
        from src.commands.registry import get_all_commands
        
        for cmd in get_all_commands():
            assert hasattr(cmd, "name")
            assert hasattr(cmd, "description")
            assert hasattr(cmd, "args_hint")
            assert cmd.name, f"Command {cmd} missing name"
            assert cmd.description, f"Command {cmd.name} missing description"

    def test_list_commands_returns_tuples(self):
        from src.commands.registry import list_commands
        
        result = list_commands()
        
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            name, desc = item
            assert name.startswith("/")
            assert isinstance(desc, str)


class TestCommandDispatch:
    """Tests for command dispatching."""

    @pytest.mark.asyncio
    async def test_dispatch_returns_none_for_non_command(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        result = await dispatch_command(
            comment_body="Just a regular comment",
            issue_id="issue-123",
            issue_identifier="ENG-1",
            user_id="user-1",
            user_name="Test User",
            background_tasks=background_tasks,
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_returns_none_for_unknown_command(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        result = await dispatch_command(
            comment_body="/unknowncommand arg1 arg2",
            issue_id="issue-123",
            issue_identifier="ENG-1",
            user_id="user-1",
            user_name="Test User",
            background_tasks=background_tasks,
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_help_command(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        with patch("src.commands.handlers.help.add_comment", new_callable=AsyncMock) as mock_comment:
            mock_comment.return_value = True
            result = await dispatch_command(
                comment_body="/help",
                issue_id="issue-123",
                issue_identifier="ENG-1",
                user_id="user-1",
                user_name="Test User",
                background_tasks=background_tasks,
            )
        
        assert result is not None
        assert result.status == "completed"
        assert result.action == "help"
        mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_ask_command_queues_task(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        result = await dispatch_command(
            comment_body="/ask How does authentication work?",
            issue_id="issue-123",
            issue_identifier="ENG-1",
            user_id="user-1",
            user_name="Test User",
            background_tasks=background_tasks,
        )
        
        assert result is not None
        assert result.status == "queued"
        assert result.action == "ask"
        background_tasks.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_ask_command_ignores_empty_question(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        result = await dispatch_command(
            comment_body="/ask",
            issue_id="issue-123",
            issue_identifier="ENG-1",
            user_id="user-1",
            user_name="Test User",
            background_tasks=background_tasks,
        )
        
        assert result is not None
        assert result.status == "ignored"
        assert "No question" in result.message
        background_tasks.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_retry_command_queues_task(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        result = await dispatch_command(
            comment_body="/retry Please focus on the backend",
            issue_id="issue-123",
            issue_identifier="ENG-1",
            user_id="user-1",
            user_name="Test User",
            background_tasks=background_tasks,
        )
        
        assert result is not None
        assert result.status == "queued"
        assert result.action == "retry"
        background_tasks.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_parsing_handles_whitespace(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        with patch("src.commands.handlers.help.add_comment", new_callable=AsyncMock) as mock_comment:
            mock_comment.return_value = True
            result = await dispatch_command(
                comment_body="  /help  ",  # Extra whitespace
                issue_id="issue-123",
                issue_identifier="ENG-1",
                user_id="user-1",
                user_name="Test User",
                background_tasks=background_tasks,
            )
        
        assert result is not None
        assert result.action == "help"

    @pytest.mark.asyncio
    async def test_command_parsing_case_insensitive(self):
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        with patch("src.commands.handlers.help.add_comment", new_callable=AsyncMock) as mock_comment:
            mock_comment.return_value = True
            result = await dispatch_command(
                comment_body="/HELP",
                issue_id="issue-123",
                issue_identifier="ENG-1",
                user_id="user-1",
                user_name="Test User",
                background_tasks=background_tasks,
            )
        
        assert result is not None
        assert result.action == "help"


class TestHelpCommand:
    """Tests for the /help command."""

    @pytest.mark.asyncio
    async def test_help_posts_comment_with_all_commands(self):
        from src.commands.handlers.help import HelpCommand
        from src.commands.command import CommandContext
        from src.commands.registry import get_all_commands
        
        background_tasks = MagicMock()
        ctx = CommandContext(
            issue_id="issue-123",
            issue_identifier="ENG-1",
            args="",
            user_id="user-1",
            user_name="Test User",
            raw_body="/help",
            background_tasks=background_tasks,
        )
        
        with patch("src.commands.handlers.help.add_comment", new_callable=AsyncMock) as mock_comment:
            mock_comment.return_value = True
            cmd = HelpCommand()
            result = await cmd.execute(ctx)
        
        assert result.status == "completed"
        
        # Check that help text includes all commands
        help_text = mock_comment.call_args[0][1]
        for cmd in get_all_commands():
            assert f"/{cmd.name}" in help_text


class TestAskCommand:
    """Tests for the /ask command."""

    @pytest.mark.asyncio
    async def test_ask_parses_model_tag(self):
        from src.commands.handlers.ask import AskCommand
        from src.commands.command import CommandContext
        
        background_tasks = MagicMock()
        ctx = CommandContext(
            issue_id="issue-123",
            issue_identifier="ENG-1",
            args="[model=opus] What is this about?",
            user_id="user-1",
            user_name="Test User",
            raw_body="/ask [model=opus] What is this about?",
            background_tasks=background_tasks,
        )
        
        cmd = AskCommand()
        result = await cmd.execute(ctx)
        
        assert result.status == "queued"
        assert result.model == "opus"

    @pytest.mark.asyncio
    async def test_ask_passes_comment_id_for_threading(self):
        """Verify /ask passes the comment_id to reply to (for threading)."""
        from src.commands.handlers.ask import AskCommand
        from src.commands.command import CommandContext
        
        background_tasks = MagicMock()
        ctx = CommandContext(
            issue_id="issue-123",
            issue_identifier="ENG-1",
            args="What is this about?",
            user_id="user-1",
            user_name="Test User",
            raw_body="/ask What is this about?",
            background_tasks=background_tasks,
            comment_id="comment-456",  # The /ask comment's ID
            parent_comment_id=None,
        )
        
        cmd = AskCommand()
        await cmd.execute(ctx)
        
        # Verify the background task was called with the comment_id as reply_to_id
        background_tasks.add_task.assert_called_once()
        call_args = background_tasks.add_task.call_args
        # args[0] is the function, args[1:] are positional args
        assert call_args[0][0].__name__ == "answer_question"
        assert call_args[0][1] == "issue-123"  # issue_id
        assert call_args[0][5] == "comment-456"  # reply_to_id (6th positional arg)


class TestRetryCommand:
    """Tests for the /retry command."""

    @pytest.mark.asyncio
    async def test_retry_parses_model_tag(self):
        from src.commands.handlers.retry import RetryCommand
        from src.commands.command import CommandContext
        
        background_tasks = MagicMock()
        ctx = CommandContext(
            issue_id="issue-123",
            issue_identifier="ENG-1",
            args="[model=sonnet] More detail please",
            user_id="user-1",
            user_name="Test User",
            raw_body="/retry [model=sonnet] More detail please",
            background_tasks=background_tasks,
        )
        
        cmd = RetryCommand()
        result = await cmd.execute(ctx)
        
        assert result.status == "queued"
        assert result.model == "sonnet"

    @pytest.mark.asyncio
    async def test_retry_works_without_feedback(self):
        from src.commands.handlers.retry import RetryCommand
        from src.commands.command import CommandContext
        
        background_tasks = MagicMock()
        ctx = CommandContext(
            issue_id="issue-123",
            issue_identifier="ENG-1",
            args="",
            user_id="user-1",
            user_name="Test User",
            raw_body="/retry",
            background_tasks=background_tasks,
        )
        
        cmd = RetryCommand()
        result = await cmd.execute(ctx)
        
        assert result.status == "queued"
        background_tasks.add_task.assert_called_once()


class TestCommentThreading:
    """Tests for comment threading support."""

    @pytest.mark.asyncio
    async def test_dispatch_passes_comment_and_parent_ids(self):
        """Verify dispatch_command passes through comment and parent IDs."""
        from src.commands.registry import dispatch_command
        
        background_tasks = MagicMock()
        
        with patch("src.commands.handlers.help.add_comment", new_callable=AsyncMock) as mock_comment:
            mock_comment.return_value = True
            result = await dispatch_command(
                comment_body="/help",
                issue_id="issue-123",
                issue_identifier="ENG-1",
                user_id="user-1",
                user_name="Test User",
                background_tasks=background_tasks,
                comment_id="comment-456",
                parent_comment_id="parent-789",
            )
        
        assert result is not None
        assert result.action == "help"

    @pytest.mark.asyncio
    async def test_context_includes_threading_fields(self):
        """Verify CommandContext includes comment_id and parent_comment_id."""
        from src.commands.command import CommandContext
        
        ctx = CommandContext(
            issue_id="issue-123",
            issue_identifier="ENG-1",
            args="test",
            user_id="user-1",
            user_name="Test User",
            raw_body="/test",
            background_tasks=MagicMock(),
            comment_id="comment-456",
            parent_comment_id="parent-789",
        )
        
        assert ctx.comment_id == "comment-456"
        assert ctx.parent_comment_id == "parent-789"

    @pytest.mark.asyncio 
    async def test_context_threading_fields_default_to_none(self):
        """Verify threading fields default to None for backwards compatibility."""
        from src.commands.command import CommandContext
        
        ctx = CommandContext(
            issue_id="issue-123",
            issue_identifier="ENG-1",
            args="test",
            user_id="user-1",
            user_name="Test User",
            raw_body="/test",
            background_tasks=MagicMock(),
        )
        
        assert ctx.comment_id is None
        assert ctx.parent_comment_id is None


class TestWebhookCommandIntegration:
    """Integration tests for slash commands via webhook."""

    @pytest.mark.asyncio
    async def test_comment_webhook_dispatches_help(self):
        from fastapi.testclient import TestClient
        from src.api import app
        
        payload = {
            "action": "create",
            "type": "Comment",
            "data": {
                "body": "/help",
                "issue": {
                    "id": "issue-123",
                    "identifier": "ENG-1",
                },
                "user": {
                    "id": "user-1",
                    "displayName": "Test User",
                },
            }
        }
        
        with patch("src.api.sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with patch("src.commands.handlers.help.add_comment", new_callable=AsyncMock) as mock_comment:
                mock_comment.return_value = True
                with TestClient(app) as client:
                    response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["action"] == "help"

    @pytest.mark.asyncio
    async def test_comment_webhook_dispatches_ask(self):
        from fastapi.testclient import TestClient
        from src.api import app
        
        payload = {
            "action": "create",
            "type": "Comment",
            "data": {
                "body": "/ask What is the authentication flow?",
                "issue": {
                    "id": "issue-123",
                    "identifier": "ENG-1",
                },
                "user": {
                    "id": "user-1",
                    "displayName": "Test User",
                },
            }
        }
        
        with patch("src.api.sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            # Mock the background task at the import location in the handler
            with patch("src.commands.handlers.ask.answer_question", new_callable=AsyncMock):
                with TestClient(app) as client:
                    response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["action"] == "ask"

    @pytest.mark.asyncio
    async def test_regular_comment_is_ignored(self):
        from fastapi.testclient import TestClient
        from src.api import app
        
        payload = {
            "action": "create",
            "type": "Comment",
            "data": {
                "body": "Just a regular comment, not a command",
                "issue": {
                    "id": "issue-123",
                    "identifier": "ENG-1",
                },
                "user": {
                    "id": "user-1",
                    "displayName": "Test User",
                },
            }
        }
        
        with patch("src.api.sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with TestClient(app) as client:
                response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
