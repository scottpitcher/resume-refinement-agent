import os

# resume_tailor.config builds `settings` at import time, so required env vars
# must be set before any test module imports it -- a per-test fixture is too late.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
