# tools/wikivoyage_collector.py
# -*- coding: utf-8 -*-

import requests
import logging
from typing import Optional
from .base_tool import BaseTool
from core.version import APP_NAME, TOOL_1_VERSION

class WikivoyageCollector(BaseTool):
    """
    Tool that collects travel information from the Wikivoyage (English) database.
    It searches for a specific location and downloads the content of the related travel guide.
    """

    def __init__(self) -> None:
        # Wiki APIs require a unique User-Agent
        self.headers = {
            'User-Agent': f'{APP_NAME}_Travel_Bot/{TOOL_1_VERSION} (contact: admin@example.com)'
        }
        self.api_url = "https://en.wikivoyage.org/w/api.php"

    def get_description(self) -> str:
        """
        Returns a short description of the tool.
        """
        return "Wikivoyage Travel Guide (Sights, Activities, Dining)"

    def run(self, input_data: str) -> str:
        """
        Executes the tool's main logic: search and extraction.

        Args:
            input_data (str): The location/city to search for.

        Returns:
            str: The raw text content of the Wikivoyage page or an error message.
        """
        location = input_data.strip()
        
        # 1. Search
        title = self._search_location(location)
        if not title:
            return f"ERROR: Location not found in Wikivoyage database: '{location}'"

        # 2. Extract Content
        content = self._get_page_content(title)
        if not content:
             return "ERROR: Page found, but content is empty."

        return content

    def _search_location(self, query: str) -> Optional[str]:
        """
        Searches for the most relevant page title.
        
        Args:
            query (str): The location name to search for.
            
        Returns:
            Optional[str]: The title of the first search result, or None if failed.
        """
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json"
        }
        try:
            resp = requests.get(self.api_url, params=params, headers=self.headers, timeout=10)
            data = resp.json()
            if data.get("query", {}).get("search"):
                # Return the title of the first result
                return data["query"]["search"][0]["title"]
        except Exception as e:
            logging.error(f"Wikivoyage search error: {e}")
        return None

    def _get_page_content(self, title: str) -> Optional[str]:
        """
        Fetches the raw text content based on the page title.
        
        Args:
            title (str): The exact title of the Wikivoyage page.
            
        Returns:
            Optional[str]: Truncated text content of the page, or None if failed.
        """
        params = {
            "action": "query",
            "prop": "extracts",
            "titles": title,
            "explaintext": 1,  # We need plain text, no HTML
            "format": "json"
        }
        try:
            resp = requests.get(self.api_url, params=params, headers=self.headers, timeout=15)
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            
            for page_id, page_data in pages.items():
                if page_id != "-1":
                    # Limit length to avoid exceeding token limits
                    full_text = page_data.get("extract", "")
                    # We pass the first 6000 characters to the AI
                    return f"PAGE TITLE: {title}\n\nCONTENT:\n{full_text[:6000]}..."
        except Exception as e:
            logging.error(f"Wikivoyage content fetch error: {e}")
        return None