# -*- coding: utf-8 -*-

"""
Abstract Base Class for AI Provider Integration.
This module defines the unified interface (AIProvider) that all concrete
implementations (e.g., OpenAI, Gemini) must follow.
It also defines the expected structure of the response object (AIResponse).
"""

from abc import ABC, abstractmethod
from typing import TypedDict, Optional, Any
from core.version import APP_NAME

# Type definition for the response structure to ensure strict type checking
class AIResponse(TypedDict):
    """Unified response format returned by AI providers."""
    response: str                   # The generated text response
    error: bool                     # Indicates if an error occurred
    input_chars: int                # Number of input characters
    output_chars: int               # Number of output characters
    input_tokens: Optional[int]     # Input tokens (if available)
    output_tokens: Optional[int]    # Output tokens (if available)
    total_tokens: Optional[int]     # Total tokens (if available)
    reasoning_tokens: Optional[int] # Chain of thought tokens (if available)
    status_message: Optional[str]   # Status message (e.g., "Success", "Error")
    thought_signature: Optional[str] # Gemini 3 encrypted thought signature (if available)


class AIProvider(ABC):
    """
    Abstract base class for all AI provider implementations.
    """

    def __init__(self, api_key: str):
        """
        Initialize the provider.
        Args:
            api_key (str): The API key for the provider.
        Raises:
            ValueError: If the key is missing or invalid.
        """
        # Validation: Ensure key is not empty and not a leftover placeholder
        if not api_key or ("YOUR_" in api_key and "_HERE" in api_key):
            raise ValueError("Invalid or unconfigured API key.")
            
        self.api_key = api_key

    @abstractmethod
    def get_response(self, model: str, prompt: str, **kwargs) -> AIResponse:
        """
        Get a response from the AI model.
        This method must be implemented by all subclasses.

        Args:
            model (str): The identifier of the model to use.
            prompt (str): The input text.
            **kwargs: Provider-specific optional parameters.

        Returns:
            AIResponse: Standardized response object (TypedDict).
        """
        pass


# --- For Testing and Demonstration ---
if __name__ == "__main__":
    print("--- AIProvider Interface Demonstration ---")

    # Mock implementation for testing
    class _MockProvider(AIProvider):
        def get_response(self, model: str, prompt: str, **kwargs) -> AIResponse:
            return {
                "response": f"Mock response from model '{model}'.",
                "error": False,
                "input_chars": len(prompt),
                "output_chars": 20,
                "input_tokens": 5,
                "output_tokens": 5,
                "total_tokens": 10,
                "reasoning_tokens": None,
                "status_message": "Success (Mock)",
                "thought_signature": None
            }

    try:
        # Proper instantiation
        provider = _MockProvider(api_key="test_valid_key")
        result = provider.get_response("test-model", "Hello AI")
        print("Successful call result:", result)

        # Testing invalid instantiation
        _ = _MockProvider(api_key="YOUR_...")
    except ValueError as e:
        print(f"Expected validation error caught: {e}")