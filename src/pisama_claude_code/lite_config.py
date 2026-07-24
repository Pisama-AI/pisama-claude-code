"""Configuration for PISAMA lite mode.

Stored at ~/.pisama/config.yaml. Provides defaults for standalone
detection without a platform backend.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from pisama_claude_code.private_files import write_private_text

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_CONFIG_DIR = Path.home() / ".pisama"
DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "pisama-lite.db"
DEFAULT_TRACES_DIR = DEFAULT_CONFIG_DIR / "traces"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"


@dataclass
class LiteConfig:
    """Configuration for PISAMA lite mode.

    All fields have sensible defaults so lite mode works out of the box
    with zero configuration.
    """

    db_path: Path = DEFAULT_DB_PATH
    traces_dir: Path = DEFAULT_TRACES_DIR
    enabled_detectors: List[str] = field(default_factory=list)  # empty = all
    severity_threshold: int = 40
    llm_judge_enabled: bool = False
    anthropic_api_key: Optional[str] = None
    judge_model: str = "claude-haiku-4-5-20251001"
    platform_url: Optional[str] = None
    platform_api_key: Optional[str] = None

    # Detection tuning
    loop_hash_threshold: int = 3
    overflow_token_limit: int = 128_000
    repetition_similarity_threshold: float = 0.7

    @classmethod
    def load_or_default(cls) -> "LiteConfig":
        """Load config from default path, or return defaults if not found."""
        if DEFAULT_CONFIG_PATH.exists():
            return cls.load(DEFAULT_CONFIG_PATH)
        return cls()

    @classmethod
    def load(cls, config_path: Path) -> "LiteConfig":
        """Load config from a YAML file.

        Args:
            config_path: Path to YAML config file.

        Returns:
            LiteConfig populated from file contents.

        Raises:
            FileNotFoundError: If config_path does not exist.
            ValueError: If YAML is malformed.
        """
        if yaml is None:
            raise ImportError(
                "PyYAML is required for lite config. Install with: pip install pyyaml"
            )

        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")

        raw = config_path.read_text()
        if not raw.strip():
            return cls()

        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError(f"Expected YAML mapping in {config_path}, got {type(data).__name__}")

        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "LiteConfig":
        """Build a LiteConfig from a dict (parsed YAML)."""
        config = cls()

        if "db_path" in data:
            config.db_path = Path(data["db_path"]).expanduser()
        if "traces_dir" in data:
            config.traces_dir = Path(data["traces_dir"]).expanduser()
        if "enabled_detectors" in data:
            val = data["enabled_detectors"]
            config.enabled_detectors = list(val) if val else []
        if "severity_threshold" in data:
            config.severity_threshold = int(data["severity_threshold"])
        if "llm_judge_enabled" in data:
            config.llm_judge_enabled = bool(data["llm_judge_enabled"])
        if "anthropic_api_key" in data:
            config.anthropic_api_key = data["anthropic_api_key"]
        if "judge_model" in data:
            config.judge_model = str(data["judge_model"])
        if "platform_url" in data:
            config.platform_url = data["platform_url"]
        if "platform_api_key" in data:
            config.platform_api_key = data["platform_api_key"]
        if "loop_hash_threshold" in data:
            config.loop_hash_threshold = int(data["loop_hash_threshold"])
        if "overflow_token_limit" in data:
            config.overflow_token_limit = int(data["overflow_token_limit"])
        if "repetition_similarity_threshold" in data:
            config.repetition_similarity_threshold = float(data["repetition_similarity_threshold"])

        return config

    def save(self, config_path: Optional[Path] = None) -> None:
        """Save config to YAML file.

        Args:
            config_path: Where to write. Defaults to ~/.pisama/config.yaml.
        """
        if yaml is None:
            raise ImportError(
                "PyYAML is required for lite config. Install with: pip install pyyaml"
            )

        path = config_path or DEFAULT_CONFIG_PATH
        write_private_text(path, self.to_yaml())

    def to_yaml(self) -> str:
        """Serialize config to YAML string."""
        if yaml is None:
            raise ImportError(
                "PyYAML is required for lite config. Install with: pip install pyyaml"
            )

        data = {
            "db_path": str(self.db_path),
            "traces_dir": str(self.traces_dir),
            "enabled_detectors": self.enabled_detectors,
            "severity_threshold": self.severity_threshold,
            "llm_judge_enabled": self.llm_judge_enabled,
            "judge_model": self.judge_model,
            "loop_hash_threshold": self.loop_hash_threshold,
            "overflow_token_limit": self.overflow_token_limit,
            "repetition_similarity_threshold": self.repetition_similarity_threshold,
        }

        # Only include secrets if they are set
        if self.anthropic_api_key:
            data["anthropic_api_key"] = self.anthropic_api_key
        if self.platform_url:
            data["platform_url"] = self.platform_url
        if self.platform_api_key:
            data["platform_api_key"] = self.platform_api_key

        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    def to_dict(self) -> dict:
        """Serialize config to a plain dict."""
        return {
            "db_path": str(self.db_path),
            "traces_dir": str(self.traces_dir),
            "enabled_detectors": self.enabled_detectors,
            "severity_threshold": self.severity_threshold,
            "llm_judge_enabled": self.llm_judge_enabled,
            "anthropic_api_key": "***" if self.anthropic_api_key else None,
            "judge_model": self.judge_model,
            "platform_url": self.platform_url,
            "platform_api_key": "***" if self.platform_api_key else None,
            "loop_hash_threshold": self.loop_hash_threshold,
            "overflow_token_limit": self.overflow_token_limit,
            "repetition_similarity_threshold": self.repetition_similarity_threshold,
        }
