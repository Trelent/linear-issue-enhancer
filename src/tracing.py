"""Real-time logging for agent tool calls."""

import json
import os
from datetime import datetime
from agents.tracing import TracingProcessor, Span


class ConsoleTracer(TracingProcessor):
    """Logs agent activity to console in real-time."""

    # Short labels for agents
    AGENT_LABELS = {
        "ContextResearcher": "CTX",
        "CodeResearcher": "CODE",
        "IssueWriter": "WRITE",
    }

    def __init__(self):
        self.current_agent: str | None = None
        self.depth = 0
        self._pending_functions: set[str] = set()
        self._span_agents: dict[str, str] = {}  # span_id -> agent name

    def _log(self, icon: str, message: str, dim: bool = False, agent: str | None = None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        # Skip ANSI codes in production (Docker/cloud) for cleaner logs
        use_ansi = os.getenv("TERM") is not None
        style = "\033[2m" if dim and use_ansi else ""
        reset = "\033[0m" if dim and use_ansi else ""
        
        # Add agent label for clarity
        label = ""
        if agent:
            short = self.AGENT_LABELS.get(agent, agent[:4].upper())
            label = f"[{short}] "
        
        print(f"{style}[{timestamp}] {label}{icon} {message}{reset}", flush=True)

    def on_span_start(self, span: Span) -> None:
        span_data = span.span_data
        span_type = type(span_data).__name__

        if span_type == "AgentSpanData":
            agent_name = getattr(span_data, "name", "Agent")
            self._span_agents[span.trace_id] = agent_name
            self._log("🤖", f"Starting {agent_name}", agent=agent_name)

        elif span_type == "FunctionSpanData":
            # Input not available yet - we'll log on span_end
            self._pending_functions.add(span.span_id)

        elif span_type == "GenerationSpanData":
            agent = self._span_agents.get(span.trace_id)
            self._log("💭", "Thinking...", dim=True, agent=agent)

    def on_span_end(self, span: Span) -> None:
        span_data = span.span_data
        span_type = type(span_data).__name__
        agent = self._span_agents.get(span.trace_id)

        if span_type == "AgentSpanData":
            agent_name = getattr(span_data, "name", "Agent")
            self._log("✅", f"Done: {agent_name}", agent=agent_name)
            # Clean up
            self._span_agents.pop(span.trace_id, None)

        elif span_type == "FunctionSpanData":
            name = getattr(span_data, "name", "unknown")
            input_val = getattr(span_data, "input", None)
            output = getattr(span_data, "output", "")

            # Log the tool call with its arguments
            display = self._format_tool_call(name, input_val)
            self._log("🔧", display, agent=agent)

            # Log result summary
            summary = self._format_tool_result(name, output)
            if summary:
                self._log("📄", summary, dim=True, agent=agent)

            self._pending_functions.discard(span.span_id)

    def _format_tool_call(self, name: str, input_val) -> str:
        """Format tool call for display."""
        args = {}
        if isinstance(input_val, dict):
            args = input_val
        elif isinstance(input_val, str) and input_val:
            try:
                args = json.loads(input_val)
            except (json.JSONDecodeError, TypeError):
                args = {}

        if name == "grep_files":
            pattern = args.get("pattern", "?")
            directory = args.get("directory", "?")
            # Show just the last part of the path
            if "/" in str(directory):
                directory = "..." + str(directory).split("/")[-2] + "/" + str(directory).split("/")[-1]
            return f"grep '{pattern}' in {directory}"

        if name == "read_file_content":
            path = args.get("file_path", "?")
            if "/" in str(path):
                path = "..." + "/".join(str(path).split("/")[-2:])
            return f"read {path}"

        if name == "list_directory":
            directory = args.get("directory", "?")
            if "/" in str(directory):
                directory = "..." + "/".join(str(directory).split("/")[-2:])
            return f"ls {directory}"

        if name == "clone_repo":
            repo = args.get("repo", "?")
            branch = args.get("branch", "")
            return f"clone {repo}" + (f" @ {branch}" if branch else "")

        if name == "list_github_repos":
            org = args.get("org", "")
            return f"list repos" + (f" for {org}" if org else "")

        if name == "list_prs":
            repo = args.get("repo", "?")
            state = args.get("state", "open")
            return f"list {state} PRs for {repo}"

        if name == "get_pr_details":
            repo = args.get("repo", "?")
            pr = args.get("pr_number", "?")
            return f"get PR #{pr} from {repo}"

        if name == "list_repo_branches":
            return f"list branches for {args.get('repo', '?')}"

        if name == "get_repo_info":
            return f"get info for {args.get('repo', '?')}"

        # Default: show name and first arg value
        if args:
            first_key = next(iter(args.keys()))
            first_val = args[first_key]
            if isinstance(first_val, str) and len(first_val) > 40:
                first_val = first_val[:40] + "..."
            return f"{name}({first_key}={first_val})"
        return name

    def _format_tool_result(self, name: str, output: str) -> str:
        """Format tool result summary."""
        if not output:
            return ""

        output_str = str(output)
        lines = output_str.splitlines()

        if name == "grep_files":
            match_count = sum(1 for line in lines if line.startswith("###"))
            if match_count:
                return f"→ found matches in {match_count} files"
            return "→ no matches"

        if name == "list_directory":
            file_count = sum(1 for line in lines if line.startswith("- `"))
            return f"→ {file_count} items"

        if name == "clone_repo":
            if "✅" in output_str:
                return "→ cloned successfully"
            return "→ clone failed"

        if name == "list_github_repos":
            repo_count = sum(1 for line in lines if line.startswith("### `"))
            return f"→ {repo_count} repos"

        if name == "list_prs":
            pr_count = sum(1 for line in lines if line.startswith("### #"))
            return f"→ {pr_count} PRs"

        if name == "read_file_content":
            return f"→ {len(lines)} lines"

        return ""

    def on_trace_start(self, trace) -> None:
        pass

    def on_trace_end(self, trace) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass
