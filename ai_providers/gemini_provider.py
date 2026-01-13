# -*- coding: utf-8 -*-

"""
Google Gemini AI Provider Implementation.
This module uses the 'google-genai' SDK to communicate with Gemini models.
It handles authentication, content generation, and token counting (metadata-based or estimated).
"""

import logging
import traceback
from typing import Any, Optional

from .base_provider import AIProvider, AIResponse
from core.version import APP_NAME
# Constant to limit the length of error logs
_MAX_LOG_ERROR_LENGTH = 500

# Conditional import of external dependencies (Graceful Degradation)
try:
    from google import genai
    from google.genai import types as genai_types
    from google.genai import errors as genai_errors
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None
    genai_types = None
    genai_errors = None
    GEMINI_AVAILABLE = False


class GeminiProvider(AIProvider):
    """
    AI Provider implementation for Google Gemini models (using google-genai SDK).
    """

    # Default safety settings: permit all content, leaving filtering to the user
    _SAFETY_SETTINGS = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    def __init__(self, api_key: str):
        """
        Initialize the Gemini client.
        Args:
            api_key (str): Valid Google API key.
        Raises:
            ImportError: If the 'google-genai' package is not installed.
            RuntimeError: If client initialization fails.
        """
        if not GEMINI_AVAILABLE:
            raise ImportError("The 'google-genai' package must be installed to use GeminiProvider.")

        super().__init__(api_key)

        try:
            self.client = genai.Client(api_key=api_key)
            logging.info(f"[{APP_NAME}] Gemini (google-genai) client successfully initialized.")
        except Exception as e:
            logging.error(f"Gemini client init error: {e}", exc_info=True)
            raise RuntimeError(f"Failed to create Gemini client: {e}") from e

    def get_response(self, model: str, prompt: str, **kwargs) -> AIResponse:
        """
        Generate a response using a Gemini model.
        Token counting is performed via response metadata or estimation.

        This method handles three distinct modes based on the model version:
        1. Standard/Lite: No thinking config.
        2. Gemini 3 Series: Uses 'thinking_level' (LOW/MEDIUM/HIGH).
        3. Gemini 2.5 Series: Uses 'thinking_budget' (Token count).

        Args:
            model (str): The model identifier.
            prompt (str): The user input prompt.
            **kwargs: Additional arguments (reasoning_effort, temperature, etc.).

        Returns:
            AIResponse: Standardized response object.
        """
        input_chars = len(prompt)
        logging.info(f"[{APP_NAME}] Gemini call ({model}). Prompt length: {input_chars} chars.")

        try:
            # --- Configuration Routing Logic ---
            gen_config: Dict[str, Any] = {}
            
            # Detect model capabilities based on naming convention
            is_gemini_3 = "gemini-3" in model
            is_gemini_2_5 = "gemini-2.5" in model
            is_lite = "lite" in model or "8b" in model
            
            # Extract reasoning effort from GUI/CLI args (default: "medium")
            # Expected values: "none", "minimal", "low", "medium", "high", "xhigh"
            gui_effort = kwargs.get("reasoning_effort", "medium").lower()

            # MODE 1: Standard / Lite (No Thinking)
            # Active if model is Lite OR user explicitly disabled reasoning
            if is_lite or gui_effort in ["none", "disabled"]:
                gen_config["temperature"] = kwargs.get("temperature", 0.7)
                # Ensure no thinking config is passed to avoid API errors
                if "thinking_config" in gen_config:
                    del gen_config["thinking_config"]

            # MODE 2: Gemini 3 Series (Thinking Levels)
            elif is_gemini_3:
                # Temperature must be 1.0 for thinking models
                gen_config["temperature"] = 1.0  
                
                target_level = "HIGH" # Default fallback
                
                # Gemini 3 Flash supports more granular levels
                if "flash" in model.lower():
                    if gui_effort == "medium": target_level = "MEDIUM"
                    elif gui_effort == "minimal": target_level = "MINIMAL"
                    elif gui_effort == "low": target_level = "LOW"
                    # high/xhigh remains HIGH
                # Gemini 3 Pro (Preview) mainly targets Low/High
                else:
                    if gui_effort in ["low", "minimal"]: target_level = "LOW"
                    # medium/high/xhigh -> HIGH
                
                gen_config["thinking_config"] = {
                    "include_thoughts": True,
                    "thinking_level": target_level
                }

            # MODE 3: Gemini 2.5 Series (Thinking Budget - Token Based)
            elif is_gemini_2_5:
                gen_config["temperature"] = 1.0 
                
                # Map GUI effort levels to specific token budgets
                budget_map = {
                    "minimal": 1024,  # Just a scratchpad
                    "low": 2048,
                    "medium": 8192,   # Sweet spot for 2.5 Pro
                    "high": 16384,
                    "xhigh": 32768    # Deep analysis (2.5 Pro only)
                }
                budget = budget_map.get(gui_effort, 8192) # Default to Medium
                
                gen_config["thinking_config"] = {
                    "include_thoughts": True,
                    "thinking_budget": budget
                }
            
            # MODE 4: Legacy / Other
            else:
                gen_config["temperature"] = kwargs.get("temperature", 0.7)

            # Create Config object
            config = genai_types.GenerateContentConfig(
                safety_settings=self._SAFETY_SETTINGS,
                **gen_config
            )

            # 1. API Call: Generate Content
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=config
            )

            response_text = response.text or ""
            output_chars = len(response_text)

            # Extract Thought Signature (if available in the new SDK response)
            thought_signature = getattr(response, "thought_signature", None)

            # ---------------------------------------------------------
            # TOKEN COUNTING LOGIC (Hybrid: Metadata + Estimation)
            # ---------------------------------------------------------
            input_tokens = None
            output_tokens = None
            total_tokens = None

            # Method 1: Try to extract exact data from response (free)
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                input_tokens = response.usage_metadata.prompt_token_count
                output_tokens = response.usage_metadata.candidates_token_count
                total_tokens = response.usage_metadata.total_token_count
                logging.debug(f"Gemini Tokens (Metadata): In={input_tokens}, Out={output_tokens}")

            # Method 2: Fallback estimation (if metadata is missing)
            if input_tokens is None:
                input_tokens = input_chars // 3
            
            if output_tokens is None:
                output_tokens = output_chars // 3

            if total_tokens is None:
                total_tokens = (input_tokens or 0) + (output_tokens or 0)
            # ---------------------------------------------------------

            logging.info(f"Gemini response OK. Output: {output_chars} chars.")

            return {
                "response": response_text,
                "error": False,
                "input_chars": input_chars,
                "output_chars": output_chars,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "reasoning_tokens": None, # Gemini API currently lumps this into output tokens
                "status_message": "Success",
                "thought_signature": thought_signature
            }

        except Exception as e:
            return self._handle_error(e, model, input_chars)

    def _handle_error(self, error: Exception, model: str, prompt_len: int) -> AIResponse:
        """
        Map Gemini-specific exceptions to the standardized response format.
        """
        error_type = type(error).__name__
        error_msg = str(error)
        
        # Detailed log for the developer
        logging.error(
            f"[{APP_NAME}] Gemini API Error ({model}): {error_type} - {error_msg}\n"
            f"Traceback: {traceback.format_exc()[:_MAX_LOG_ERROR_LENGTH]}"
        )

        # Generate user-friendly message
        gui_message = f"Error: General API error ({model})."

        if isinstance(error, genai_errors.APIError):
            gui_message = f"Error: Provider API error: {error.message}"
        elif "API key" in error_msg or "401" in error_msg:
            gui_message = "Error: Invalid API key. Please check your settings."
        elif "429" in error_msg or "ResourceExhausted" in error_msg:
             gui_message = "Error: Rate Limit Exceeded (429). Please wait a moment."

        return {
            "response": gui_message,
            "error": True,
            "input_chars": prompt_len,
            "output_chars": 0,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "reasoning_tokens": None,
            "status_message": "API Error",
            "thought_signature": None
        }