# -*- coding: utf-8 -*-

"""
Configuration Manager Module.

This class handles loading, validating, and populating the `config.json` file
with default values. It also initializes the system-wide logging configuration.
"""

import json
import os
import logging
from typing import Any, Dict, Optional, List
from core.version import APP_NAME, CORE_VERSION

# Default filenames
DEFAULT_CONFIG_FILENAME = 'config.json'
DEFAULT_LOG_FILENAME = f'{APP_NAME.lower().replace(" ", "_")}.log'


class ConfigManager:
    """
    Manages application settings and the lifecycle of config.json.
    """

    # Default Configuration Template
    # Includes support for OpenAI, Gemini, Anthropic, DeepSeek, Mistral, and DeepInfra
    _DEFAULT_CONFIG_TEMPLATE: Dict[str, Any] = {
        "api_keys": {
            "Gemini-1": "YOUR_GEMINI_1_API_KEY_HERE",
            "Gemini-2": "YOUR_GEMINI_2_API_KEY_HERE",
            "Gemini-3": "YOUR_GEMINI_3_API_KEY_HERE",
            "OpenAI": "YOUR_OPENAI_API_KEY_HERE",
            "DeepSeek": "YOUR_DEEPSEEK_API_KEY_HERE",
            "Mistral": "YOUR_MISTRAL_API_KEY_HERE",
            "DeepInfra": "YOUR_DEEPINFRA_API_KEY_HERE",
            "Anthropic": "YOUR_ANTHROPIC_API_KEY_HERE"
        },
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
            "timeout_keep_alive": 5
        },
        "models": {
            "Gemini-1": [
                # --- Efficient / Lite Models (No Thinking) ---
                {"name": "gemini-2.5-flash-lite", "supports_google_search_tool": False},
                
                # --- Standard Hybrid Models (Token Budget Based Thinking) ---
                {"name": "gemini-2.5-flash", "supports_google_search_tool": True},
                {"name": "gemini-2.5-pro", "supports_google_search_tool": True},
                
                # --- Next Gen Thinking Models (Level Based Thinking) ---
                {"name": "gemini-3-flash-preview", "supports_google_search_tool": True},
                # Note: 'gemini-3-pro' might be preview or standard depending on release
                {"name": "gemini-3-pro-preview", "supports_google_search_tool": True},
                
                # --- Legacy / Fallback ---
                {"name": "gemini-2.0-flash", "supports_google_search_tool": True}
            ],
            "Gemini-2": [
                # --- Efficient / Lite Models (No Thinking) ---
                {"name": "gemini-2.5-flash-lite", "supports_google_search_tool": False},
                
                # --- Standard Hybrid Models (Token Budget Based Thinking) ---
                {"name": "gemini-2.5-flash", "supports_google_search_tool": True},
                {"name": "gemini-2.5-pro", "supports_google_search_tool": True},
                
                # --- Next Gen Thinking Models (Level Based Thinking) ---
                {"name": "gemini-3-flash-preview", "supports_google_search_tool": True},
                # Note: 'gemini-3-pro' might be preview or standard depending on release
                {"name": "gemini-3-pro-preview", "supports_google_search_tool": True},
                
                # --- Legacy / Fallback ---
                {"name": "gemini-2.0-flash", "supports_google_search_tool": True}
            ],
            "Gemini-3": [
                # --- Efficient / Lite Models (No Thinking) ---
                {"name": "gemini-2.5-flash-lite", "supports_google_search_tool": False},
                
                # --- Standard Hybrid Models (Token Budget Based Thinking) ---
                {"name": "gemini-2.5-flash", "supports_google_search_tool": True},
                {"name": "gemini-2.5-pro", "supports_google_search_tool": True},
                
                # --- Next Gen Thinking Models (Level Based Thinking) ---
                {"name": "gemini-3-flash-preview", "supports_google_search_tool": True},
                # Note: 'gemini-3-pro' might be preview or standard depending on release
                {"name": "gemini-3-pro-preview", "supports_google_search_tool": True},
                
                # --- Legacy / Fallback ---
                {"name": "gemini-2.0-flash", "supports_google_search_tool": True}
            ],
            "OpenAI": [
                "gpt-5.2",             # New Standard
                "gpt-5.2-pro",         # Flagship (Filtered by AppController safety switch)
                "gpt-5.1",             # Fallback
                "gpt-5",               # Stable base model
                "gpt-5-mini",          # Fast variant
                "o1",                  # Reasoning (formerly preview)
                "gpt-4o"               # Legacy flagship
            ],
            "DeepSeek": [
                "deepseek-chat",       # Points to V3
                "deepseek-reasoner"    # R1 (Reasoning) model
            ],
            "Mistral": [
                "mistral-large-latest",
                "mistral-small-latest",
                "codestral-latest"
            ],
            "Anthropic": [
                "claude-sonnet-4-5",     # New flagship
                "claude-haiku-4-5",      # Fastest model
                "claude-opus-4-1"        # Complex reasoning successor
            ],
            "DeepInfra": [
                "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                "meta-llama/Llama-3.3-70B-Instruct",
                "deepseek-ai/DeepSeek-V3",
                "Qwen/Qwen2.5-72B-Instruct"
            ]
        },
        "defaults": {
            "provider": "Gemini-1",
            "model": "gemini-2.5-flash",
            "reasoning_effort": "medium",
            "batch_delay_seconds": 1.0,
            "use_dynamic_loader": False
        },
        "dynamic_loader": {
            "timeout_ms": 30000,
            "scroll": False,
            "max_scrolls": 10,
            "wait_selector": "",
            "remove_selectors": [
                "#onetrust-accept-btn-handler", 
                ".cookie-banner", 
                ".ad-container"
            ]
        },
        "limits": {
            "concatenated_max_chars": 1000000,
            "download_max_size_mb": 10
        }
    }

    def __init__(self, config_file: str = DEFAULT_CONFIG_FILENAME, headless_mode: bool = False):
        """
        Initialize ConfigManager.
        Args:
            config_file (str): Path to the configuration file.
            headless_mode (bool): (Deprecated) Kept for compatibility; GUI logic has been removed.
        """
        self.config_file = config_file
        self.config: Dict[str, Any] = {}
        self.is_loaded = False

        # Load immediately upon initialization
        try:
            self._load_config()
            self.is_loaded = True
        except Exception as e:
            # Critical error: Log it, but allow the caller to handle the failure state
            logging.critical(f"[{APP_NAME}] ConfigManager init failed: {e}")
            print(f"CRITICAL CONFIG ERROR: {e}")

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        """
        Safely retrieve a value from the configuration.
        Args:
            key (str): The key to search for.
            default: Return value if the key does not exist.

        Returns:
            The configuration value or the default.
        """
        return self.config.get(key, default)

    def _load_config(self) -> None:
        """
        Loads configuration from file or creates a default one.
        Raises:
            IOError, json.JSONDecodeError: If file handling fails.
        """
        if not os.path.exists(self.config_file):
            self._create_default_config()
        
        with open(self.config_file, 'r', encoding='utf-8') as f:
            raw_config = json.load(f)

        # Validate and normalize
        self.config = self._normalize_config(raw_config)
        
        # Setup system components based on config
        self._setup_logging()
        self._ensure_output_directory()
        
        logging.info(f"Configuration loaded: {self.config_file}")

    def _create_default_config(self) -> None:
        """Creates the config.json file from the default template."""
        logging.warning(f"Config file not found. Creating: {self.config_file}")
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._DEFAULT_CONFIG_TEMPLATE, f, indent=4, ensure_ascii=False)
        except IOError as e:
            logging.error(f"Failed to write config file: {e}")
            raise

    def _normalize_config(self, loaded_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Supplies missing keys and corrects data structures.
        """
        # 1. Ensure default sections exist (using setdefault for shallow merge)
        for section, default_content in self._DEFAULT_CONFIG_TEMPLATE.items():
            if section not in loaded_cfg:
                loaded_cfg[section] = default_content
            elif isinstance(default_content, dict):
                # Merge missing keys one level deep (e.g., adding 'dynamic_loader' settings)
                for key, val in default_content.items():
                    loaded_cfg[section].setdefault(key, val)

        # 2. Specific fix: Gemini models (convert str list -> dict list)
        models = loaded_cfg.get('models', {})
        for provider, model_list in models.items():
            if provider.startswith("Gemini-") and isinstance(model_list, list):
                normalized_list = []
                for item in model_list:
                    if isinstance(item, str):
                        normalized_list.append({"name": item, "supports_google_search_tool": False})
                    elif isinstance(item, dict):
                        normalized_list.append(item)
                models[provider] = normalized_list
        
        return loaded_cfg

    def _setup_logging(self) -> None:
        """Configures logging (resets handlers to avoid duplication)."""
        log_file = self.config.get('paths', {}).get('log_file', DEFAULT_LOG_FILENAME)
        
        # Clear existing handlers (important for reloads or tests)
        root_logger = logging.getLogger()
        if root_logger.handlers:
            for handler in root_logger.handlers[:]:
                handler.close()
                root_logger.removeHandler(handler)
        
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            force=True
        )

    def _ensure_output_directory(self) -> None:
        """Creates the output directory."""
        out_dir = self.config.get('paths', {}).get('output_directory', 'ai_responses')
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            logging.error(f"Cannot create output directory ({out_dir}): {e}")
            # Non-fatal error; program can run, but saving results will fail