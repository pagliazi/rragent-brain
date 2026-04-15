"""
Three-level configuration: defaults → YAML file → environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "gateway": {
        "url": "ws://127.0.0.1:18789",
        "auth_token": "",
        "agent_id": "rragent",
    },
    "redis": {
        "url": "redis://127.0.0.1:6379/0",
    },
    "hermes": {
        "agent_path": os.getenv("HERMES_AGENT_PATH", os.path.expanduser("~/hermes-agent")),
        "model": "qwen3.5-plus",
        "provider": "anthropic",
        "max_workers": 4,
        "default_toolsets": ["core", "web", "terminal"],
    },
    "providers": {
        "primary": "qwen3.5-plus",
        "fallback_chain": [
            "dashscope/qwen3.5-plus",
            "ollama/qwen2.5-coder:14b",
        ],
        "fallback_trigger": 3,
    },
    "context": {
        "max_tokens": 200000,
        "autocompact_threshold": 0.8,
        "tool_result_max_chars": 50000,
    },
    "resilience": {
        "api_max_retries": 10,
        "api_base_delay_ms": 500,
        "api_max_backoff_ms": 32000,
        "circuit_breaker_threshold": 3,
        "health_check_interval": 10,
    },
    "evolution": {
        "background_review": True,
        "memory_nudge_interval": 10,
        "skill_nudge_interval": 10,
        "evolution_check_interval": 300,
    },
    "session": {
        "dir": os.path.expanduser("~/.rragent/sessions"),
        "rotation_size": 262144,  # 256KB
    },
    "skills": {
        "auto_sync": True,
        "sync_interval_hours": 6,
    },
    "reachrich": {
        "base_url": "",              # Bridge API endpoint, set via REACHRICH_URL
        "token": "",                 # User auth token (from ReachRich 设置 → API Token)
        "bridge_client_path": "",    # Path to directory containing bridge_client.py
        "stream_verify_hmac": True,  # Verify HMAC signatures on Redis Stream messages
    },
}

ENV_OVERRIDES = {
    "GATEWAY_URL": ("gateway", "url"),
    "GATEWAY_TOKEN": ("gateway", "auth_token"),
    "REDIS_URL": ("redis", "url"),
    "HERMES_AGENT_PATH": ("hermes", "agent_path"),
    "HERMES_MODEL": ("hermes", "model"),
    "HERMES_PROVIDER": ("hermes", "provider"),
    "RRAGENT_PRIMARY_MODEL": ("providers", "primary"),
    "RRAGENT_SESSION_DIR": ("session", "dir"),
    "REACHRICH_URL": ("reachrich", "base_url"),
    "REACHRICH_TOKEN": ("reachrich", "token"),
    "BRIDGE_CLIENT_PATH": ("reachrich", "bridge_client_path"),
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load config with three-level merge: defaults → YAML → env."""
    config = DEFAULT_CONFIG.copy()

    # Level 2: YAML file
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            yaml_config = yaml.safe_load(f) or {}
        config = deep_merge(config, yaml_config)

    # Level 3: Environment variables
    for env_key, (section, key) in ENV_OVERRIDES.items():
        val = os.getenv(env_key)
        if val is not None:
            if section not in config:
                config[section] = {}
            config[section][key] = val

    return config


@dataclass
class RRClawConfig:
    """Typed configuration wrapper."""
    raw: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, *keys: str, default: Any = None) -> Any:
        d = self.raw
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, default)
            else:
                return default
        return d

    @classmethod
    def from_file(cls, path: str | None = None) -> "RRClawConfig":
        return cls(raw=load_config(path))
