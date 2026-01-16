"""Shared utilities for comment threading."""


def get_reply_target(comment_id: str | None, parent_comment_id: str | None) -> str | None:
    """Determine which comment to reply to for proper threading.
    
    Linear only supports one level of nesting - replies must be to top-level comments.
    
    Args:
        comment_id: ID of the comment containing the slash command
        parent_comment_id: ID of parent comment if the slash command is itself a reply
    
    Returns:
        The comment ID to reply to, or None if no threading
    
    Logic:
        - If the command is a reply to another comment: reply to that parent
          (we can't reply to a reply, only to top-level comments)
        - If the command is a top-level comment: reply to it
          (creates a new thread under the command)
    """
    if parent_comment_id:
        return parent_comment_id
    return comment_id
