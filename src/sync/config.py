import os

# Email domain for identifying internal team members
INTERNAL_DOMAIN = os.getenv("INTERNAL_DOMAIN", "trelent.com")

# Slack token can be either:
# - User token (xoxp-...) - accesses channels you're a member of, no bot needed
# - Bot token (xoxb-...) - requires adding bot to each channel
#
# For user tokens, create an app at api.slack.com/apps with these User Token Scopes:
#   channels:history, channels:read, groups:history, groups:read, users:read, users:read.email

