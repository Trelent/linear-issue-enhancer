import os

# Email domains for identifying internal team members (comma-separated)
# Example: "trelent.com,trelent.io,acme.com"
_domains = os.getenv("INTERNAL_DOMAINS", "trelent.com")
INTERNAL_DOMAINS = set(d.strip() for d in _domains.split(",") if d.strip())


def is_internal_email(email: str) -> bool:
    """Check if an email belongs to an internal domain."""
    if not email or "@" not in email:
        return False
    domain = email.split("@")[1].lower()
    return domain in INTERNAL_DOMAINS


# Slack token can be either:
# - User token (xoxp-...) - accesses channels you're a member of, no bot needed
# - Bot token (xoxb-...) - requires adding bot to each channel
#
# For user tokens, create an app at api.slack.com/apps with these User Token Scopes:
#   channels:history, channels:read, groups:history, groups:read, users:read, users:read.email
