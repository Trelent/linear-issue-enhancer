import subprocess
import shutil
from pathlib import Path

from agents import function_tool


@function_tool
def grep_files(pattern: str, directory: str, file_glob: str = "*.md") -> str:
    """Search for a pattern in files using grep.

    Args:
        pattern: The regex pattern to search for.
        directory: The directory to search in.
        file_glob: File pattern to match (default: *.md).
    """
    try:
        result = subprocess.run(
            ["grep", "-r", "-n", "-i", "--include", file_glob, pattern, directory],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip()
        return output if output else "No matches found."
    except subprocess.TimeoutExpired:
        return "Search timed out."
    except Exception as e:
        return f"Error: {e}"


@function_tool
def read_file_content(file_path: str, max_lines: int = 200) -> str:
    """Read the contents of a file.

    Args:
        file_path: Path to the file to read.
        max_lines: Maximum number of lines to return (default: 200).
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return f"File not found: {file_path}"
        
        lines = path.read_text().splitlines()[:max_lines]
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading file: {e}"


@function_tool
def list_directory(directory: str) -> str:
    """List files and directories in a path.

    Args:
        directory: The directory to list.
    """
    try:
        path = Path(directory)
        if not path.exists():
            return f"Directory not found: {directory}"
        
        items = sorted(path.iterdir())
        return "\n".join(str(item.relative_to(path)) for item in items[:100])
    except Exception as e:
        return f"Error: {e}"


@function_tool
def clone_repo(repo_url: str, target_dir: str) -> str:
    """Clone a GitHub repository.

    Args:
        repo_url: The GitHub repository URL (e.g., https://github.com/user/repo).
        target_dir: The directory to clone into.
    """
    try:
        if Path(target_dir).exists():
            shutil.rmtree(target_dir)
        
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, target_dir],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return f"Clone failed: {result.stderr}"
        return f"Successfully cloned to {target_dir}"
    except subprocess.TimeoutExpired:
        return "Clone timed out."
    except Exception as e:
        return f"Error: {e}"

