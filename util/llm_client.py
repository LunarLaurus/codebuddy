import yaml
import logging
from laurus_llm.client.llm_helpers import LLMWrapper

# ---------------- Logging ----------------
LOG = logging.getLogger("laurus-llm")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


# ---------------- Config ----------------
def load_config(path="config.yaml"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        LOG.info("Config loaded from %s", path)
        return cfg
    except FileNotFoundError:
        LOG.warning("Config file %s not found; using defaults", path)
        return {}
    except Exception as e:
        LOG.error("Failed to load config: %s", e)
        return {}


_config = load_config()
LOCAL_LLM_URL = _config.get("local_llm_url", "http://localhost:8000")
DEFAULT_MAX_TOKENS = int(_config.get("default_max_tokens", 800))
DEFAULT_TIMEOUT = float(_config.get("default_timeout_second", 30))
DEFAULT_TEMP = float(_config.get("default_temperature", 0.3))

LOG.info(
    "Local LLM URL: %s | Max Tokens: %d | Temperature: %s",
    LOCAL_LLM_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMP,
)

# ---------------- LLM client wrapper ----------------
llm = LLMWrapper(base_url=LOCAL_LLM_URL, timeout=DEFAULT_TIMEOUT, poll_interval=10)


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
    LOG.debug("Generating summary: prompt length=%d", len(prompt))
    try:
        result = llm.summarize_code(
            user_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )
        LOG.info("Summary generated successfully")
        return result
    except Exception as e:
        LOG.exception("Failed to generate LLM summary")
        raise e


# ---------------- Optional convenience ----------------
def set_summarizer_mode(mode: str, custom_system_prompt: str = None):
    """Switch summarization mode: 'c', 'asm', 'file', or 'custom'"""
    LOG.info("Setting summarizer mode: %s", mode)
    try:
        return llm.set_mode(mode, custom_system_prompt)
    except Exception as e:
        LOG.exception("Failed to set summarizer mode")
        raise e


def set_mode_file():
    LOG.info("Setting summarizer mode: file")
    set_summarizer_mode("file", None)


def set_mode_c():
    LOG.info("Setting summarizer mode: c")
    set_summarizer_mode("c", None)


def set_mode_asm():
    LOG.info("Setting summarizer mode: asm")
    set_summarizer_mode("asm", None)


def reload_model(model_id: str = None):
    """Reload the LLM model from HF (default reloads current)"""
    LOG.info("Reloading model: %s", model_id or "current")
    try:
        return llm.reload_model(model_id)
    except Exception as e:
        LOG.exception("Failed to reload model")
        raise e


def check_health():
    """Check server health"""
    LOG.debug("Checking LLM server health")
    try:
        health = llm.health()
        LOG.info("LLM server health: %s", health)
        return health
    except Exception as e:
        LOG.exception("Failed to check server health")
        raise e
