# .test/test_llm_client.py
import sys
from pathlib import Path

# Add project root to Python path so 'util' and other packages can be imported
sys.path.append(str(Path(__file__).resolve().parent.parent))

import logging

from util.llm_client import (
    check_health,
    generate_llm_summary,
    reload_model,
    set_summarizer_mode,
)


LOG = logging.getLogger("codebuddy-llm-tests")
logging.basicConfig(level=logging.INFO)


def test_health_check():
    """Directly test the check_health function in llmclient.py"""
    health = check_health()
    LOG.info("Health check returned: %s", health)
    assert isinstance(health, dict)
    assert health.get("status") in ("ok", True)


def test_generate_summary():
    """Directly test generate_llm_summary from llmclient.py"""
    prompt = "def add(a, b): return a + b"
    summary = generate_llm_summary(prompt)
    LOG.info("Generated summary: %s", summary)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_set_mode():
    """Directly test set_summarizer_mode"""
    result = set_summarizer_mode("c")
    assert isinstance(result, dict)
    assert result.get("mode") == "c"

    result2 = set_summarizer_mode("asm")
    assert isinstance(result2, dict)
    assert result2.get("mode") == "asm"


def test_reload_model():
    """Directly test reload_model"""
    result = reload_model()
    assert result is None or isinstance(result, dict)
