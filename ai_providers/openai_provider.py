# -*- coding: utf-8 -*-

"""
OpenAI and OpenAI-Compatible API Provider Implementation.

This module provides a generic interface for any service that adheres to the
OpenAI API specification (e.g., DeepSeek, Mistral, DeepInfra, Anthropic).
It supports both the standard Chat Completions API and the newer Responses API (GPT-5+).
"""

import logging
import traceback
from typing import Dict, Any, Optional, List

from .base_provider import AIProvider, AIResponse
from core.version import APP_NAME

# Constant for logging
_MAX_LOG_ERROR_LENGTH = 500

# Import external library with error handling
try:
    from openai import OpenAI, APIStatusError, BadRequestError, RateLimitError
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    APIStatusError = None
    BadRequestError = None
    RateLimitError = None
    OPENAI_AVAILABLE = False


class OpenAICompatibleProvider(AIProvider):
    """
    Flexible provider for handling OpenAI and compatible APIs (DeepSeek, Mistral, Anthropic, etc.).
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None, provider_name: str = "OpenAI"):
        """
        Initialize the OpenAI client.
        Args:
            api_key (str): API key.
            base_url (Optional[str]): Custom endpoint (if not using the official OpenAI API).
            provider_name (str): Name of the provider for logging purposes (e.g., "DeepSeek", "Anthropic").
        """
        if not OPENAI_AVAILABLE:
            raise ImportError("The 'openai' package must be installed to use OpenAICompatibleProvider.")

        super().__init__(api_key)
        self.provider_name = provider_name
        self.base_url = base_url

        try:
            self.client = OpenAI(api_key=api_key, base_url=base_url)
            logging.info(f"[{APP_NAME}] OpenAI client configured: {provider_name} (URL: {base_url or 'Default'})")
        except Exception as e:
            logging.error(f"Client init error ({provider_name}): {e}", exc_info=True)
            raise RuntimeError(f"Failed to configure client ({provider_name}): {e}") from e

    def get_response(self, model: str, prompt: str, **kwargs) -> AIResponse:
        """
        Get response using the appropriate API endpoint.
        Uses 'Responses API' for GPT-5/o-series and 'Chat Completions' for legacy/compatible models.
        """
        input_chars = len(prompt)
        
        # Detect GPT-5 and O-series (Requires the new v1/responses endpoint)
        use_responses_api = self.provider_name == "OpenAI" and (model.startswith('o') or model.startswith('gpt-5'))

        try:
            if use_responses_api:
                return self._call_responses_api(model, prompt, input_chars, **kwargs)
            else:
                return self._call_chat_api(model, prompt, input_chars, **kwargs)

        except (APIStatusError, BadRequestError, RateLimitError, Exception) as e:
            return self._handle_error(e, model, input_chars)

    def _call_chat_api(self, model: str, prompt: str, input_chars: int, **kwargs) -> AIResponse:
        """
        Standard Chat Completions API call (GPT-4, DeepSeek, Mistral, Claude).
        """
        logging.info(f"[{APP_NAME}] {self.provider_name} Chat call ({model}). Prompt: {input_chars} chars.")
        
        messages = [{"role": "user", "content": prompt}]
        
        # Base parameters
        params = {
            "model": model,
            "messages": messages
        }
        
        # Pass optional parameters (e.g., temperature)
        if "temperature" in kwargs:
            params["temperature"] = kwargs["temperature"]

        # --- ANTHROPIC (CLAUDE) SPECIFIC LOGIC ---
        # Integration for Anthropic models happens HERE, via the OpenAI SDK compatibility layer.
        # ARCHITECTURAL DECISION:
        # 1. Text-In / Text-Out: The application handles heavy lifting (PDF, DOCX, HTML cleaning)
        #    in the `text_extractor.py` module, so only clean text is sent to the API.
        #    Therefore, native Anthropic SDK file handling is not required.
        # 2. Compatibility: Anthropic's official "OpenAI Compatibility Layer" covers our needs perfectly,
        #    avoiding an additional dependency (the 'anthropic' package).
        # 3. Thinking Mode: Although the OpenAI SDK doesn't natively support Claude's "thinking" param,
        #    we solve this by passing it through `extra_body`.

        if self.provider_name == "Anthropic":
            # Translate 'reasoning_effort' (low, medium, high) from GUI/CLI
            effort = kwargs.get("reasoning_effort", "medium")
            
            # Enable thinking mode only if not 'none' or 'minimal'
            if effort not in ["none", "minimal"]:
                # Determine token budget based on 'level' (Anthropic budget tokens)
                budget_map = {
                    "low": 2000,
                    "medium": 4000,
                    "high": 8000
                }
                budget = budget_map.get(effort, 4000)

                # Use 'extra_body' to pass Claude-specific parameters
                # which the OpenAI SDK would otherwise not recognize.
                params["extra_body"] = {
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": budget
                    }
                }
                
                # IMPORTANT: In Thinking mode, temperature is usually fixed at 1.0 or invalid.
                # Remove it if it was set to avoid conflicts.
                if "temperature" in params:
                    del params["temperature"]

                # SAFETY: max_tokens must always be greater than the budget!
                # If caller didn't specify max_tokens, set a safe buffer (budget + 4k).
                if "max_tokens" not in kwargs:
                    params["max_tokens"] = budget + 4000

        # --- Provider-specific extra parameters (e.g., DeepInfra stop tokens) ---
        params.update(self._get_provider_specific_params(model))

        # API Call
        response = self.client.chat.completions.create(**params)
        
        content = response.choices[0].message.content or ""
        usage = response.usage

        # Safely extract token statistics
        p_tokens = usage.prompt_tokens if usage else 0
        c_tokens = usage.completion_tokens if usage else 0
        t_tokens = usage.total_tokens if usage else 0

        logging.info(f"{self.provider_name} response OK. Output: {len(content)} chars.")

        return {
            "response": content,
            "error": False,
            "input_chars": input_chars,
            "output_chars": len(content),
            "input_tokens": p_tokens,
            "output_tokens": c_tokens,
            "total_tokens": t_tokens,
            "reasoning_tokens": None,
            "status_message": "Success",
            "thought_signature": None
        }

    def _call_responses_api(self, model: str, prompt: str, input_chars: int, **kwargs) -> AIResponse:
        """
        Handle GPT-5.1 and Responses API (o1, o3).
        The new Responses API requires stricter input formats and parameters.
        """
        logging.info(f"[{APP_NAME}] OpenAI Responses API call ({model}).")

        # 1. Input Format: MUST be a list [{"role": "user" ...}]
        api_input = [{"role": "user", "content": prompt}]

        # 2. Reasoning Config (New parameter in 2025 API)
        default_effort = "none" if "gpt-5.1" in model else "medium"
        effort = kwargs.get("reasoning_effort", default_effort)

        api_params = {
            "model": model,
            "input": api_input,
            "reasoning": {"effort": effort}
        }

        # 3. Verbosity (New parameter in GPT-5.1)
        if "verbosity" in kwargs:
            api_params["text"] = {"verbosity": kwargs["verbosity"]}

        # 4. Max Output Tokens (Replaces max_tokens in Responses API)
        if "max_tokens" in kwargs:
            api_params["max_output_tokens"] = kwargs["max_tokens"]
        else:
            api_params["max_output_tokens"] = 25000

        # 5. Parameter cleaning
        if effort != "none":
            # Temperature is handled by reasoning effort in many cases
            pass 
        elif effort == "none" and "temperature" in kwargs:
            api_params["temperature"] = kwargs["temperature"]

        # API Call (client.responses.create)
        response = self.client.responses.create(**api_params)

        content = response.output_text or ""
        usage = getattr(response, 'usage', None)
        
        r_tokens = 0
        if usage and hasattr(usage, 'output_tokens_details'):
            r_tokens = getattr(usage.output_tokens_details, 'reasoning_tokens', 0)

        status = getattr(response, 'status', 'complete')
        status_msg = "Success (Responses API)" if status != "incomplete" else "Partial response"

        return {
            "response": content,
            "error": False,
            "input_chars": input_chars,
            "output_chars": len(content),
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
            "total_tokens": usage.total_tokens if usage else 0,
            "reasoning_tokens": r_tokens,
            "status_message": status_msg,
            "thought_signature": None
        }

    def _get_provider_specific_params(self, model: str) -> Dict[str, Any]:
        """
        Returns provider-specific extra parameters (e.g., stop sequences).
        """
        params = {}
        if self.provider_name == "DeepInfra":
            if "llama-3" in model.lower():
                params["stop"] = ["<|eot_id|>"]
            elif "mistral" in model.lower() and "instruct" in model.lower():
                params["stop"] = ["[/INST]"]
        return params

    def _handle_error(self, error: Exception, model: str, prompt_len: int) -> AIResponse:
        """
        Map OpenAI errors to a unified format.
        """
        error_type = type(error).__name__
        error_msg = str(error)
        
        full_trace = traceback.format_exc()
        logging.error(
            f"{self.provider_name} API Error ({model}): {error_type}\n"
            f"Details: {full_trace[:_MAX_LOG_ERROR_LENGTH]}"
        )

        gui_msg = f"Error: General API error ({self.provider_name})."

        if isinstance(error, RateLimitError) or getattr(error, 'status_code', 0) == 429:
            gui_msg = f"Error: Rate limit exceeded ({self.provider_name}). Please try again later."
        elif isinstance(error, APIStatusError) and error.status_code == 401:
            gui_msg = f"Error: API key error ({self.provider_name})."
        elif isinstance(error, BadRequestError) and "context_length" in error_msg.lower():
            gui_msg = "Error: Input is too long for this model."
        elif "connection" in error_msg.lower():
            gui_msg = "Error: Network connection failure."

        return {
            "response": gui_msg,
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