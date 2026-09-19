"""Root-level test configuration and shared fixtures."""

import json
import os
import re
from types import SimpleNamespace
from typing import Any, Dict, List

import json_repair
import pytest

from videocaptioner.core.asr.asr_data import ASRData, ASRDataSeg
from videocaptioner.core.translate import SubtitleProcessData, TargetLanguage
from videocaptioner.core.utils import cache

# Disable cache for testing
cache.disable_cache()


@pytest.fixture
def sample_asr_data():
    """Create sample ASR data for translation testing."""
    segments = [
        ASRDataSeg(start_time=0, end_time=1000, text="I am a student"),
        ASRDataSeg(start_time=1000, end_time=2000, text="You are a teacher"),
        ASRDataSeg(start_time=2000, end_time=3000, text="VideoCaptioner is a tool for captioning videos"),
    ]
    return ASRData(segments)


@pytest.fixture
def sample_translate_data():
    """Create sample translation data for testing."""
    return [
        SubtitleProcessData(index=1, original_text="I am a student", translated_text=""),
        SubtitleProcessData(index=2, original_text="You are a teacher", translated_text=""),
        SubtitleProcessData(index=3, original_text="VideoCaptioner is a tool for captioning videos", translated_text=""),
    ]


@pytest.fixture
def target_language():
    """Default target language for translation tests."""
    return TargetLanguage.SIMPLIFIED_CHINESE


@pytest.fixture
def check_env_vars():
    """Check if required environment variables are set."""
    def _check(*var_names):
        missing = [var for var in var_names if not os.getenv(var)]
        if missing:
            pytest.skip(f"Required environment variables not set: {', '.join(missing)}")
    return _check


# ============================================================================
# Fake LLM client
# ============================================================================

# Marker phrase used by the sentence-splitting prompt
_SPLIT_MARKER = "following sentence:"
# Per-segment unit limit for the fake splitter; low enough to satisfy every
# max_word_count used by production prompts (their minimum is 12)
_SPLIT_UNIT_LIMIT = 5
# One token = a latin/digit word, a whitespace run, or a single character
# (CJK chars and punctuation each arrive as single characters)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|\s+|.", re.UNICODE)


def _fake_split_text(text: str) -> str:
    """Greedily split text into <br>-separated segments, preserving content."""
    segments: List[str] = []
    buf: List[str] = []
    count = 0

    def flush() -> None:
        segment = "".join(buf).strip()
        if segment:
            segments.append(segment)
        buf.clear()

    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        if token.isspace():
            buf.append(token)
            continue
        if count >= _SPLIT_UNIT_LIMIT:
            flush()
            count = 0
        buf.append(token)
        count += 1
    flush()

    return "<br>".join(segments) if segments else text


def _fake_llm_content(messages: List[dict]) -> str:
    """Produce a deterministic response body for the given chat messages.

    Dispatch by message shape:
    - sentence splitting: echo the text back with <br> separators
    - translation / optimization agent loops: echo the subtitle dict back
    - anything else (e.g. single-item translation): echo the user message
    """
    # Sentence splitting prompt asks to separate "the following sentence"
    for message in messages:
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            _, marker, text = content.partition(_SPLIT_MARKER + "\n")
            if marker and text.strip():
                return _fake_split_text(text)

    # Agent loops pass a subtitle dict; return it unchanged so key-set and
    # content-similarity validations pass on the first round
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if "<input_subtitle>" in content:
            inner = content.split("<input_subtitle>", 1)[1]
            inner = inner.split("</input_subtitle>", 1)[0]
            return json.dumps(json_repair.loads(inner), ensure_ascii=False)
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data and all(
            isinstance(value, str) for value in data.values()
        ):
            return json.dumps(data, ensure_ascii=False)

    # Fallback: echo the last user message
    for message in reversed(messages):
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            return content
    return ""


class FakeLLMClient:
    """Minimal OpenAI-compatible client returning deterministic responses."""

    def __init__(self):
        self.calls: List[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(
        self,
        *,
        model: str,
        messages: List[dict],
        temperature: float = 1.0,
        **kwargs: Any,
    ):
        self.calls.append(
            {"model": model, "messages": messages, "temperature": temperature}
        )
        content = _fake_llm_content(messages)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            model=model,
        )


@pytest.fixture
def mock_llm_client(monkeypatch) -> FakeLLMClient:
    """Replace the global OpenAI client with an offline fake.

    All production LLM traffic funnels through
    ``videocaptioner.core.llm.client.call_llm`` -> ``get_llm_client()``, which
    reads the module-level ``_global_client`` singleton at call time. Patching
    that singleton therefore covers the splitter, optimizer, translators and
    background threads without touching each call site.
    """
    from videocaptioner.core.llm import client as llm_client_module

    fake = FakeLLMClient()
    monkeypatch.setattr(llm_client_module, "_global_client", fake)

    # SubtitleThread validates the LLM endpoint with a real network call
    # before doing any work; bypass that for mocked tests
    try:
        from videocaptioner.ui.thread import subtitle_thread

        def _fake_setup_llm_config(self):
            """Skip endpoint validation and return the config unchanged."""
            return self.task.subtitle_config

        monkeypatch.setattr(
            subtitle_thread.SubtitleThread,
            "_setup_llm_config",
            _fake_setup_llm_config,
        )
    except ImportError:
        pass

    # call_llm is memoized against the persistent LLM cache; drop whatever the
    # fake produced so mock responses never leak into real runs. Cache keys
    # may be unhashable (lists), so track them by equality, not by set.
    llm_cache = cache.get_llm_cache()
    keys_before = list(llm_cache)
    yield fake
    for key in list(llm_cache):
        if key not in keys_before:
            llm_cache.delete(key)


@pytest.fixture
def expected_translations() -> Dict[str, Dict[str, List[str]]]:
    """Expected translation keywords for quality validation."""
    return {
        "简体中文": {
            "I am a student": ["学生"],
            "You are a teacher": ["老师", "教师"],
            "VideoCaptioner is a tool for captioning videos": ["工具"],
        },
        "日本語": {
            "I am a student": ["学生"],
            "You are a teacher": ["先生", "教師"],
        },
        "English": {
            "我是学生": ["student"],
            "你是老师": ["teacher"],
        },
    }


def assert_translation_quality(original: str, translated: str, expected_keywords: List[str]) -> None:
    """Validate translation contains expected keywords."""
    assert translated, f"Translation is empty for: {original}"
    found_keywords = [kw for kw in expected_keywords if kw in translated]
    assert found_keywords, (
        f"Translation quality issue:\n"
        f"  Original: {original}\n"
        f"  Translated: {translated}\n"
        f"  Expected keywords: {expected_keywords}"
    )
