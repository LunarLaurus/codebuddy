import yaml
from laurus_local_llm.llm_helpers import LLMWrapper


# ---------------- Config ----------------
def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_config = load_config()
LOCAL_LLM_URL = _config.get("local_llm_url", "http://localhost:8000")
DEFAULT_MAX_TOKENS = int(_config.get("default_max_tokens", 800))
DEFAULT_TEMP = float(_config.get("default_temperature", 0.3))

# ---------------- LLM client wrapper ----------------
llm = LLMWrapper(base_url=LOCAL_LLM_URL)


# ---------------- Summarizer function ----------------
def generate_llm_summary(
    prompt: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMP,
    system_prompt: str = None,
) -> str:
    """
    Submit code/text to the local LLM, wait for result, and return summary.
    Optionally override system_prompt (mode-specific).
    """
    return llm.summarize_code(
        user_prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        system_prompt=system_prompt,
    )


# ---------------- Optional convenience ----------------
def set_summarizer_mode(mode: str, custom_system_prompt: str = None):
    """Switch summarization mode: 'c', 'asm', 'file', or 'custom'"""
    return llm.set_mode(mode, custom_system_prompt)


def reload_model(model_id: str = None):
    """Reload the LLM model from HF (default reloads current)"""
    return llm.reload_model(model_id)


def check_health():
    """Check server health"""
    return llm.health()
