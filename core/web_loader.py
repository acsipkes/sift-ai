# -*- coding: utf-8 -*-

"""
Web Loader Module.

This module is responsible for fetching content from URLs.
It supports two retrieval strategies:
1. Static (requests): Fast, suitable for standard HTML and binary files (PDF, DOCX).
2. Dynamic (playwright): Slower, suitable for JS-heavy sites (SPA, Infinite Scroll).

This module follows a "Soft Dependency" pattern: it will not crash if
'requests' or 'playwright' are missing, but will report an error at runtime.
"""

import os
import time
import logging
import tempfile
import shutil
from urllib.parse import urlparse
from typing import Tuple, Optional, Dict, Any, List, Union
from core.version import APP_NAME, CORE_VERSION

# --- Optional Dependency: Static Engine (Requests) ---
try:
    import requests
    from requests.exceptions import RequestException
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    RequestException = None
    REQUESTS_AVAILABLE = False

# --- Optional Dependency: Dynamic Engine (Playwright) ---
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    PlaywrightTimeout = None
    PLAYWRIGHT_AVAILABLE = False

# Constants
DEFAULT_USER_AGENT = f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 {APP_NAME}/{CORE_VERSION}'
DEFAULT_TIMEOUT_MS = 30000


class WebLoader:
    """
    Centralized class for fetching web content.
    """

    def __init__(self, config_limits: Dict[str, Any]):
        """
        Initialize the WebLoader.

        Args:
            config_limits (Dict): The 'limits' section from config.json.
        """
        self.logger = logging.getLogger(__name__)
        
        # Convert MB limit to Bytes (Default: 10 MB)
        max_mb = config_limits.get("download_max_size_mb", 10)
        self.max_size_bytes = max_mb * 1024 * 1024

    def fetch(self, url: str, use_dynamic: bool, options: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], str, Optional[str]]:
        """
        Main entry point. Dispatches the request to the appropriate engine.

        Args:
            url (str): The target URL.
            use_dynamic (bool): If True, uses Playwright.
            options (Dict, optional): Extra settings (scroll, wait_selector, etc.).

        Returns:
            Tuple[Result, Info, Error]:
                - Result (str | None): The HTML content OR the file path to a downloaded binary.
                - Info (str): Description of the source (e.g., "URL (Dynamic)").
                - Error (str | None): Error message if failed, else None.
        """
        options = options or {}
        self.logger.info(f"WebLoader fetch initiated: {url} (Dynamic Mode: {use_dynamic})")

        try:
            # 1. Dynamic Path (Playwright)
            if use_dynamic:
                if not PLAYWRIGHT_AVAILABLE:
                    return None, url, "Playwright is missing. Install via 'pip install playwright' and 'playwright install'."
                return self._fetch_dynamic(url, options)

            # 2. Static Path (Requests)
            else:
                if not REQUESTS_AVAILABLE:
                    return None, url, "The 'requests' library is missing."
                return self._fetch_static(url)

        except Exception as e:
            self.logger.critical(f"WebLoader Critical Error: {e}", exc_info=True)
            return None, url, f"Critical download error: {str(e)}"

    # -------------------------------------------------------------------------
    # STATIC ENGINE (Requests)
    # -------------------------------------------------------------------------

    def _fetch_static(self, url: str) -> Tuple[Optional[str], str, Optional[str]]:
        """Handles static HTML and binary file downloads."""
        try:
            # 'stream=True' is vital to prevent loading large files into memory immediately
            response = requests.get(
                url, 
                stream=True, 
                timeout=20, 
                headers={'User-Agent': DEFAULT_USER_AGENT}
            )
            response.raise_for_status()

            content_type = response.headers.get('content-type', '').lower()

            # Content-Length check (if provided by server)
            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > self.max_size_bytes:
                return None, url, f"File size ({int(content_length)} bytes) exceeds the limit."

            # CASE A: HTML Content
            if 'text/html' in content_type:
                # We read .text (this loads content into memory).
                # To be strictly safe against massive HTML, we could iterate, 
                # but standard practice for HTML allows .text with a length check.
                text = response.text
                if len(text.encode('utf-8')) > self.max_size_bytes:
                    return None, url, "HTML content exceeded size limit."
                
                return text, "URL (Static HTML)", None

            # CASE B: Binary File (PDF, DOCX, etc.)
            else:
                return self._download_binary_file(response, url)

        except RequestException as e:
            return None, url, f"Network Error (Requests): {e}"
        except Exception as e:
            return None, url, f"Download Error: {e}"

    def _download_binary_file(self, response: requests.Response, url: str) -> Tuple[Optional[str], str, Optional[str]]:
        """Streams binary data to a temporary file."""
        # Create a temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".tmp")
        downloaded_size = 0
        
        try:
            with temp_file as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if downloaded_size > self.max_size_bytes:
                            raise ValueError("Download exceeded size limit during streaming.")
            
            temp_path = temp_file.name
            
            # Detect extension from URL path (fallback to .pdf)
            path_ext = os.path.splitext(urlparse(url).path)[1]
            if not path_ext:
                path_ext = ".pdf" # Assumption for binary non-html content
            
            final_path = temp_path + path_ext
            
            # Rename/Move (shutil.move handles cross-device moves better than os.rename)
            try:
                os.rename(temp_path, final_path)
            except OSError:
                shutil.move(temp_path, final_path)
                
            return final_path, f"URL (Binary File: {path_ext})", None

        except Exception as e:
            # Cleanup on failure
            if os.path.exists(temp_file.name):
                try:
                    os.remove(temp_file.name)
                except OSError:
                    pass
            return None, url, f"Binary download failed: {e}"

    # -------------------------------------------------------------------------
    # DYNAMIC ENGINE (Playwright)
    # -------------------------------------------------------------------------

    def _fetch_dynamic(self, url: str, options: Dict[str, Any]) -> Tuple[Optional[str], str, Optional[str]]:
        """Uses Playwright to render the page, handling JS and scrolling."""
        
        # Unpack options with defaults
        timeout = options.get('timeout_ms', DEFAULT_TIMEOUT_MS)
        wait_selector = options.get('wait_selector')
        do_scroll = options.get('scroll', False)
        max_scrolls = options.get('max_scrolls', 10)
        remove_selectors = options.get('remove_selectors', [])

        try:
            with sync_playwright() as p:
                # Launch browser in headless mode
                # Chromium is the safest default for general web scraping
                browser = p.chromium.launch(headless=True)
                
                # Create context (User-Agent is critical to avoid bot detection)
                context = browser.new_context(user_agent=DEFAULT_USER_AGENT)
                page = context.new_page()

                self.logger.info(f"Playwright navigating to: {url}")
                
                # 1. Navigation
                # 'domcontentloaded' is faster than 'networkidle'
                page.goto(url, timeout=timeout, wait_until="domcontentloaded")

                # 2. Wait for specific element (Optional)
                if wait_selector:
                    try:
                        self.logger.info(f"Waiting for selector: {wait_selector}")
                        page.wait_for_selector(wait_selector, timeout=5000)
                    except PlaywrightTimeout:
                        self.logger.warning(f"Selector ({wait_selector}) did not appear in time. Continuing.")

                # 3. Clean DOM via JS (Remove cookie banners, ads, etc.)
                if remove_selectors:
                    self._remove_elements_js(page, remove_selectors)

                # 4. Smart Scroll (Infinite Scroll handling)
                if do_scroll:
                    self._smart_scroll(page, max_scrolls)

                # 5. Extract Content
                content = page.content()
                
                # Size Check
                if len(content.encode('utf-8')) > self.max_size_bytes:
                     return None, url, "Rendered HTML content exceeded size limit."

                browser.close()
                return content, "URL (Dynamic JS)", None

        except PlaywrightTimeout:
            return None, url, "Timeout occurred during dynamic loading."
        except Exception as e:
            return None, url, f"Playwright Error: {e}"

    def _smart_scroll(self, page, max_scrolls: int):
        """Scrolls to the bottom of the page incrementally."""
        self.logger.info(f"Starting Smart Scroll (Max: {max_scrolls})")
        
        last_height = page.evaluate("document.body.scrollHeight")
        
        for i in range(max_scrolls):
            # Scroll to bottom
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            
            # Static sleep is often more reliable than networkidle for infinite scrolls
            time.sleep(1.5)
            
            new_height = page.evaluate("document.body.scrollHeight")
            
            if new_height == last_height:
                self.logger.debug("Page height did not change. Stopping scroll.")
                break
            
            last_height = new_height
            self.logger.debug(f"Scroll step: {i+1}/{max_scrolls}")

    def _remove_elements_js(self, page, selectors: List[str]):
        """Removes elements from the DOM using client-side JavaScript."""
        if not selectors:
            return
            
        self.logger.info(f"Removing elements via JS: {selectors}")
        
        # JS Closure to iterate and remove
        js_code = """(selectors) => {
            selectors.forEach(selector => {
                const elements = document.querySelectorAll(selector);
                elements.forEach(el => el.remove());
            });
        }"""
        
        try:
            page.evaluate(js_code, selectors)
        except Exception as e:
            self.logger.warning(f"Error removing elements: {e}")

# --- Testing Block (Run this file directly to test) ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Mock Config
    mock_limits = {"download_max_size_mb": 50}
    loader = WebLoader(mock_limits)
    
    print("--- WebLoader Test ---")
    
    # Test 1: Static (if requests is installed)
    if REQUESTS_AVAILABLE:
        print("Testing Static Fetch...")
        c, i, e = loader.fetch("https://example.com", use_dynamic=False)
        print(f"Info: {i}, Error: {e}, Length: {len(c) if c else 0}")
    
    # Test 2: Dynamic (if playwright is installed)
    if PLAYWRIGHT_AVAILABLE:
        print("\nTesting Dynamic Fetch...")
        c, i, e = loader.fetch("https://example.com", use_dynamic=True, options={'scroll': False})
        print(f"Info: {i}, Error: {e}, Length: {len(c) if c else 0}")