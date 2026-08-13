"""
Interactive-confirmation implementations satisfying the ToolConfirmCallback
signature used by pipeline.ResumeTailoringPipeline.run(confirm_tools=...).

Swap in a different implementation (e.g. one backed by a web request/response
instead of a terminal prompt) by passing it to run() -- pipeline.py itself
never imports input()/print() for this purpose directly.
"""

from typing import Any, Dict, List


def default_terminal_tool_confirmation(missing: List[Dict[str, Any]]) -> List[str]:
    """Prints a numbered list of missing tools and reads a comma-separated list
    of chosen indices from stdin. Blank input means none confirmed. Non-numeric
    or out-of-range tokens are ignored with a warning, never a crash."""
    if not missing:
        return []

    print("\nThe following JD tools weren't found in your resume's Tools line:")
    for i, t in enumerate(missing, start=1):
        context = t.get("context", "")
        print(f"  {i}. {t['name']}  ({t.get('importance', '?')})" + (f"  -- {context}" if context else ""))

    raw = input("Enter comma-separated numbers you're competent in (blank for none): ").strip()
    if not raw:
        return []

    confirmed = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            print(f"  (ignoring unrecognized input: {token!r})")
            continue
        idx = int(token)
        if 1 <= idx <= len(missing):
            confirmed.append(missing[idx - 1]["name"])
        else:
            print(f"  (ignoring out-of-range number: {idx})")
    return confirmed
