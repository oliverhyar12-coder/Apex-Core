"""Known provider presets.

A preset fills in the adapter, base URL and API-key variable for a well-known
service, so ``apex init --provider deepseek`` works without looking anything up.
Model names change often, so presets only *suggest* a model where one is a
long-lived alias; ``apex init`` always writes the chosen model into the
configuration explicitly, and ``apex models`` lists what the provider offers.

All presets have been exercised against mocked HTTP responses only. Live
compatibility depends on each service and is listed in the README.
"""

from __future__ import annotations

from dataclasses import dataclass

OPENAI_COMPATIBLE = "openai-compatible"
ANTHROPIC = "anthropic"
ADAPTERS = (OPENAI_COMPATIBLE, ANTHROPIC)


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    label: str
    adapter: str
    base_url: str
    api_key_env: str | None
    suggested_model: str | None = None
    key_url: str | None = None
    notes: str = ""


PRESETS: dict[str, ProviderPreset] = {
    preset.name: preset
    for preset in (
        ProviderPreset(
            name="openai",
            label="OpenAI",
            adapter=OPENAI_COMPATIBLE,
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            key_url="https://platform.openai.com/api-keys",
        ),
        ProviderPreset(
            name="anthropic",
            label="Anthropic (native Messages API)",
            adapter=ANTHROPIC,
            base_url="https://api.anthropic.com/v1",
            api_key_env="ANTHROPIC_API_KEY",
            suggested_model="claude-opus-5-5",
            key_url="https://console.anthropic.com/settings/keys",
        ),
        ProviderPreset(
            name="deepseek",
            label="DeepSeek",
            adapter=OPENAI_COMPATIBLE,
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            suggested_model="deepseek-chat",
            key_url="https://platform.deepseek.com/api_keys",
        ),
        ProviderPreset(
            name="gemini",
            label="Google Gemini (OpenAI-compatible endpoint)",
            adapter=OPENAI_COMPATIBLE,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key_env="GEMINI_API_KEY",
            key_url="https://aistudio.google.com/apikey",
            notes="Uses Google's OpenAI-compatibility layer, not the native Gemini API.",
        ),
        ProviderPreset(
            name="mistral",
            label="Mistral AI",
            adapter=OPENAI_COMPATIBLE,
            base_url="https://api.mistral.ai/v1",
            api_key_env="MISTRAL_API_KEY",
            key_url="https://console.mistral.ai/api-keys",
        ),
        ProviderPreset(
            name="openrouter",
            label="OpenRouter",
            adapter=OPENAI_COMPATIBLE,
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            key_url="https://openrouter.ai/keys",
        ),
        ProviderPreset(
            name="groq",
            label="Groq",
            adapter=OPENAI_COMPATIBLE,
            base_url="https://api.groq.com/openai/v1",
            api_key_env="GROQ_API_KEY",
            key_url="https://console.groq.com/keys",
        ),
        ProviderPreset(
            name="ollama",
            label="Ollama (local)",
            adapter=OPENAI_COMPATIBLE,
            base_url="http://localhost:11434/v1",
            api_key_env=None,
            notes="Runs models locally; no API key or per-token cost. Tool calling "
            "depends on the model.",
        ),
        ProviderPreset(
            name="lmstudio",
            label="LM Studio (local)",
            adapter=OPENAI_COMPATIBLE,
            base_url="http://localhost:1234/v1",
            api_key_env=None,
            notes="Runs models locally; no API key or per-token cost.",
        ),
    )
}


def get_preset(name: str) -> ProviderPreset | None:
    return PRESETS.get(name.lower())
