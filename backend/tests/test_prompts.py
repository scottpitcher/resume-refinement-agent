from resume_tailor import prompts

PROMPT_NAMES = [
    "PICK_RESUME_SYSTEM",
    "ROLE_SLUG_SYSTEM",
    "EXTRACT_JD_SYSTEM",
    "PROPOSE_EDITS_SYSTEM",
    "FACT_CHECK_SYSTEM",
    "SCORE_SYSTEM",
]


def test_all_prompts_defined_and_nonempty():
    for name in PROMPT_NAMES:
        value = getattr(prompts, name)
        assert isinstance(value, str)
        assert value.strip()


def test_prompts_request_json_only():
    for name in PROMPT_NAMES:
        assert "JSON" in getattr(prompts, name)
