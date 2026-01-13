# -*- coding: utf-8 -*-

"""
Application Controller Module.

This module coordinates the interaction between the user interface (GUI/CLI)
and the backend logic (text extraction, AI API calls). It manages threads,
message queues, and resource handling.
"""

import queue
import threading
import os
import time
import re
import datetime
import logging
from typing import List, Optional, Dict, Any, Tuple, Callable, Union
from urllib.parse import urlparse

# Project modules
from core.version import APP_NAME, CORE_VERSION
from config_manager import ConfigManager
import core.text_extractor as text_extractor
from core.web_loader import WebLoader
from ai_providers.base_provider import AIProvider, AIResponse
from ai_providers.gemini_provider import GeminiProvider, GEMINI_AVAILABLE
from ai_providers.openai_provider import OpenAICompatibleProvider, OPENAI_AVAILABLE


class AppController:
    """
    Central controller class.
    Manages the instantiation of AI providers and controls the processing workflow.
    Delegates content fetching to WebLoader and text extraction to TextExtractor.
    """

    # --- Constants for processing modes ---
    MODE_DIRECT = "Direct Input"
    MODE_SINGLE_FILE = "Single File"
    MODE_BATCH_FILES = "Batch Files"
    MODE_URL = "URL"
    MODE_BATCH_DIR = "Batch Directory"
    MODE_BATCH_URL_LIST = "Batch URL List"
    
    # --- COST SAFETY SWITCH ---
    # Controls access to high-cost models (e.g., GPT-5.2 Pro).
    # set to True only when explicitly needed.
    ENABLE_GPT_5_2_PRO = False

    def __init__(self, config_manager: ConfigManager):
        """
        Initialize the Controller.
        Args:
            config_manager (ConfigManager): The loaded configuration manager instance.
        """
        self.config_manager = config_manager
        logging.info(f"Initializing {APP_NAME} v{CORE_VERSION} Controller")
        self.message_queue: queue.Queue = queue.Queue()
        self.providers: Dict[str, AIProvider] = {}
        
        # Initialize the WebLoader with limits from config
        limits = self.config_manager.get("limits", {})
        self.web_loader = WebLoader(config_limits=limits)
        
        self._initialize_providers()
        
    def get_models_for_provider(self, provider_key: str) -> List[str]:
        """
        Returns the available models for a provider, filtering out restricted 
        (high-cost) versions based on the safety switch.
        
        Args:
            provider_key (str): The key identifier for the AI provider.

        Returns:
            List[str]: A list of allowed model names.
        """
        # 1. Raw list from configuration
        all_models = self.config_manager.get("models", {}).get(provider_key, [])
        
        # Normalization (handle both dict objects and strings)
        model_names = []
        for m in all_models:
            if isinstance(m, dict):
                model_names.append(m.get("name"))
            else:
                model_names.append(m)

        # 2. Safety Filter (Cost Guardrail)
        filtered_models = []
        for model in model_names:
            # If the model name contains "pro" AND the safety switch is FALSE
            # (Assuming the config uses "gpt-5.2-pro")
            if "gpt-5.2-pro" in model and not self.ENABLE_GPT_5_2_PRO:
                logging.debug(f"Model hidden due to safety switch: {model}")
                continue
            
            filtered_models.append(model)

        return filtered_models

    def _initialize_providers(self):
        """
        Initialize AI providers based on API keys and available libraries.
        """
        api_keys = self.config_manager.get("api_keys", {})
        models_cfg = self.config_manager.get("models", {})

        for provider_key in models_cfg.keys():
            api_key = self._get_api_key(provider_key, api_keys)
            
            if not api_key:
                logging.debug(f"Skipping {provider_key}: API key not found.")
                continue

            try:
                self._register_provider(provider_key, api_key)
            except Exception as e:
                logging.warning(f"Failed to initialize provider ({provider_key}): {e}")

    def _get_api_key(self, provider_key: str, config_keys: Dict[str, str]) -> Optional[str]:
        """
        Retrieve API key from environment variables, then configuration.
        
        Args:
            provider_key (str): The provider identifier.
            config_keys (Dict[str, str]): Dictionary of keys from config.

        Returns:
            Optional[str]: The API key if found and valid, else None.
        """
        # 1. Environment variable (e.g., OPENAI_API_KEY)
        env_var = f"{provider_key.upper().replace('-', '_')}_API_KEY"
        key = os.environ.get(env_var)
        
        # 2. Config file
        if not key:
            key = config_keys.get(provider_key)

        # 3. Fallback to OpenAI key for compatible providers
        if not key and provider_key in ["DeepSeek", "Mistral", "DeepInfra"]:
            key = os.environ.get("OPENAI_API_KEY") or config_keys.get("OpenAI")

        # --- VALIDATION LOGIC ---
        # reject empty keys or keys that are suspiciously short
        if not key or len(key) < 8:
            return None

        # Detect default configuration placeholders (e.g., "YOUR_OPENAI_API_KEY_HERE")
        if "YOUR_" in key and "_HERE" in key:
            logging.warning(f"Skipping {provider_key}: Default placeholder detected in config.")
            return None

        return key

    def _register_provider(self, key: str, api_key: str):
        """
        Instantiate and register a specific provider.

        Args:
            key (str): The provider key.
            api_key (str): The valid API key.
        """
        if key.startswith("Gemini-") and GEMINI_AVAILABLE:
            self.providers[key] = GeminiProvider(api_key)
        elif key == "OpenAI" and OPENAI_AVAILABLE:
            self.providers[key] = OpenAICompatibleProvider(api_key, provider_name="OpenAI")
        elif key == "Anthropic" and OPENAI_AVAILABLE:
            self.providers[key] = OpenAICompatibleProvider(
                api_key, 
                base_url="https://api.anthropic.com/v1/", 
                provider_name="Anthropic"
            )
        elif key in ["DeepSeek", "Mistral", "DeepInfra"] and OPENAI_AVAILABLE:
            urls = {
                "DeepSeek": "https://api.deepseek.com/v1",
                "Mistral": "https://api.mistral.ai/v1",
                "DeepInfra": "https://api.deepinfra.com/v1/openai"
            }
            self.providers[key] = OpenAICompatibleProvider(api_key, base_url=urls.get(key), provider_name=key)
        
        if key in self.providers:
            logging.info(f"Provider activated: {key}")

    def get_available_providers(self) -> List[str]:
        """Returns the list of active providers."""
        return sorted(list(self.providers.keys()))

    # --- GUI Processing Control ---

    def start_processing(self, mode: str, prompt: str, provider_key: str, model: str, options: Dict[str, Any]):
        """
        Start processing in a separate thread (to prevent GUI freezing).
        
        Args:
            mode (str): The processing mode constant.
            prompt (str): The user's prompt.
            provider_key (str): Selected provider.
            model (str): Selected model.
            options (Dict[str, Any]): Additional processing options.
        """
        # Dispatch table for modes
        dispatch_map: Dict[str, Tuple[Callable, Tuple]] = {
            self.MODE_DIRECT: (self._process_direct_input, (prompt, provider_key, model, options)),
            self.MODE_SINGLE_FILE: (self._process_single_file, (options.get('file_path'), prompt, provider_key, model, options)),
            self.MODE_BATCH_FILES: (self._process_batch_files, (options.get('file_paths'), prompt, provider_key, model, options)),
            self.MODE_URL: (self._process_url, (options.get('url'), prompt, provider_key, model, options)),
            self.MODE_BATCH_DIR: (self._process_batch_directory, (options.get('dir_path'), options.get('file_type'), options.get('recursive'), prompt, provider_key, model, options)),
            self.MODE_BATCH_URL_LIST: (self._process_batch_url_list, (options.get('urls'), prompt, provider_key, model, options)),
        }

        if mode in dispatch_map:
            func, args = dispatch_map[mode]
            threading.Thread(target=func, args=args, daemon=True).start()
        else:
            self._report_error(f"Unknown mode: {mode}", provider_key, model)

    # --- GUI Worker Methods ---

    def _process_direct_input(self, prompt: str, provider: str, model: str, options: Dict):
        self.message_queue.put(("status", "Generating AI response..."))
        # Key: is_batch=False
        self._run_ai_task(provider, model, prompt, "Direct Input", is_batch=False, **options)

    def _process_single_file(self, path: str, prompt: str, provider: str, model: str, options: Dict):
        if not path or not os.path.isfile(path):
            self._report_error("Invalid file path.", provider, model)
            return

        filename = os.path.basename(path)
        self.message_queue.put(("status", f"Reading: {filename}..."))
        
        content = text_extractor.extract_text_from_file(path, options.get('html_options'))
        if not content:
            self._report_error(f"Empty or unreadable file: {filename}", provider, model)
            return

        full_prompt = self._build_prompt(prompt, content, f"FILE: {filename}")
        self._run_ai_task(provider, model, full_prompt, f"FILE: {filename}", is_batch=False, original_filename=filename, **options)

    def _process_batch_files(self, paths: List[str], prompt: str, provider: str, model: str, options: Dict):
        if not paths:
            self._report_error("No files selected.", provider, model)
            return

        self.message_queue.put(("status", "Concatenating files..."))
        contents = []
        for path in paths:
            text = text_extractor.extract_text_from_file(path, options.get('html_options'))
            if text:
                contents.append(f"--- {os.path.basename(path)} ---\n{text}")
        
        if not contents:
            self._report_error("Failed to extract content from files.", provider, model)
            return

        full_prompt = self._build_prompt(prompt, "\n\n".join(contents), "BATCH FILES")
        self._run_ai_task(provider, model, full_prompt, f"Concatenated ({len(contents)} items)", is_batch=False, **options)

    def _process_url(self, url: str, prompt: str, provider: str, model: str, options: Dict):
        content, info, error = self._fetch_content_from_url(
            url, 
            options.get('send_raw_html', False), 
            options.get('html_options', {}),
            options.get('dynamic_options')
        )
        if error:
            self._report_error(error, provider, model, source_info=info)
            return

        self.message_queue.put(("status", "Generating AI response..."))
        full_prompt = self._build_prompt(prompt, content, info)
        self._run_ai_task(provider, model, full_prompt, info, is_batch=False, **options)

    def _process_batch_directory(self, dir_path: str, file_type: str, recursive: bool, prompt: str, provider: str, model: str, options: Dict):
        files = self._scan_directory(dir_path, file_type, recursive)
        if not files:
            self.message_queue.put(("status", "No matching files found in directory."))
            self.message_queue.put(("batch_complete", None))
            return

        self._execute_batch_loop(files, prompt, provider, model, options, is_url_mode=False)

    def _process_batch_url_list(self, urls: List[str], prompt: str, provider: str, model: str, options: Dict):
        if not urls:
            self._report_error("URL list is empty.", provider, model)
            return
        
        self._execute_batch_loop(urls, prompt, provider, model, options, is_url_mode=True)

    # --- GUI Batch Logic ---

    def _execute_batch_loop(self, items: List[str], prompt: str, provider: str, model: str, options: Dict, is_url_mode: bool):
        """Generic loop for batch processing (URL list or File list) in GUI MODE."""
        total = len(items)
        self.message_queue.put(("batch_start", total))
        delay = options.get("delay", 1.0)
        
        # Extract options for URL fetching
        raw_html = options.get('send_raw_html', False)
        html_opts = options.get('html_options', {})
        dynamic_opts = options.get('dynamic_options')

        for i, item in enumerate(items):
            # Update display
            display_name = item[:60] if is_url_mode else os.path.basename(item)
            self.message_queue.put(("batch_progress", (i, total, display_name)))

            # Extract content
            content = None
            source_info = ""
            original_name = None
            
            if is_url_mode:
                content, source_info, error = self._fetch_content_from_url(item, raw_html, html_opts, dynamic_opts)
            else:
                content = text_extractor.extract_text_from_file(item, html_opts)
                source_info = f"FILE: {display_name}"
                original_name = display_name
                error = "Read failure" if content is None else None

            # AI call or report error
            if content:
                full_prompt = self._build_prompt(prompt, content, source_info)
                self._run_ai_task(provider, model, full_prompt, source_info, is_batch=True, batch_item_id=item, original_filename=original_name, **options)
            else:
                self._report_error(error or "Unknown error", provider, model, source_info, is_batch=True, batch_id=item)

            # Delay
            if i < total - 1 and delay > 0:
                time.sleep(delay)
        
        # self.message_queue.put(("batch_complete", None)) # GUI handles batch completion

    # --- Core AI Execution (GUI) ---

    def _run_ai_task(self, provider_key: str, model: str, prompt: str, source_info: str, is_batch: bool, batch_item_id=None, original_filename=None, **kwargs):
        """
        Executes the AI call, saves the result, and notifies the GUI via QUEUE.
        """
        provider = self.providers.get(provider_key)
        if not provider:
            self._report_error(f"Provider unavailable: {provider_key}", provider_key, model, source_info, is_batch, batch_item_id)
            return

        # Call Provider
        try:
            # Filter kwargs (pass only relevant data to provider)
            ai_kwargs = {k: v for k, v in kwargs.items() if k in ['reasoning_effort', 'verbosity']}
            response_dict: AIResponse = provider.get_response(model, prompt, **ai_kwargs)
        except Exception as e:
            logging.error(f"Critical error during AI call: {e}")
            self._report_error(f"Exception occurred: {e}", provider_key, model, source_info, is_batch, batch_item_id)
            return

        # Process result
        saved_path = None
        if not response_dict["error"]:
            saved_path = self._save_result(provider_key, model, prompt, response_dict["response"], source_info, is_batch, batch_item_id, original_filename)

        # Notify GUI
        payload = (response_dict, source_info, saved_path, batch_item_id)
        msg_type = "batch_item_result" if is_batch else "single_result"
        self.message_queue.put((msg_type, payload))

    # --- Helper Functions (Used by both GUI and Headless) ---

    def _fetch_content_from_url(self, url: str, raw_html: bool, html_opts: Dict, dynamic_opts: Dict = None) -> Tuple[Optional[str], str, Optional[str]]:
        """
        Delegates URL downloading to WebLoader.
        Handles both static and dynamic fetching strategies.
        
        Args:
            url (str): The target URL.
            raw_html (bool): If True, skips text extraction (only applies if result is HTML).
            html_opts (Dict): Configuration for BeautifulSoup text extraction.
            dynamic_opts (Dict, optional): Configuration for Playwright (enabled, scroll, etc.).

        Returns:
            Tuple[Content (str|None), SourceInfo (str), ErrorMessage (str|None)]
        """
        # 1. Fetch content (Delegated to WebLoader)
        self.message_queue.put(("status", f"Downloading: {url[:50]}..."))
        
        use_dynamic = dynamic_opts.get('enabled', False) if dynamic_opts else False
        
        # WebLoader returns either the content string OR a temporary file path
        content_or_path, source_info, error_msg = self.web_loader.fetch(url, use_dynamic, dynamic_opts)

        if error_msg:
            return None, source_info, error_msg

        # 2. Process/Extract Content
        extracted_text = None
        processing_error = None

        try:
            # Case A: Result is a File Path (e.g., PDF downloaded to temp)
            if os.path.exists(content_or_path) and os.path.isfile(content_or_path):
                try:
                    extracted_text = text_extractor.extract_text_from_file(content_or_path, html_opts)
                    if not extracted_text:
                        processing_error = "Extraction failed (empty content)."
                finally:
                    # CRITICAL: Clean up the temporary file created by WebLoader
                    try:
                        os.remove(content_or_path)
                    except OSError as e:
                        logging.warning(f"Failed to delete temp file {content_or_path}: {e}")

            # Case B: Result is HTML String
            else:
                if raw_html:
                    extracted_text = content_or_path
                else:
                    extracted_text = text_extractor.extract_text_from_html_content(content_or_path, html_opts)
                    if not extracted_text:
                        processing_error = "Empty text after HTML extraction."

        except Exception as e:
            processing_error = f"Processing error: {str(e)}"

        if processing_error:
            return None, source_info, processing_error
            
        return extracted_text, source_info, None

    def _save_result(self, provider, model, prompt, response, source, is_batch, batch_id, orig_filename) -> Optional[str]:
        """Saves the response to a file."""
        # Use overridden output dir if set (headless), otherwise config
        out_dir = getattr(self, '_headless_output_dir', None) or \
                  self.config_manager.get("paths", {}).get("output_directory", "ai_responses")
        
        os.makedirs(out_dir, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Filename generation
        if orig_filename:
            name_base = os.path.splitext(orig_filename)[0]
        elif batch_id and "http" in batch_id:
            # Simple sanitization for URLs
            name_base = "url_" + re.sub(r'\W+', '_', batch_id.split('//')[-1])[:30]
        else:
            name_base = "response"

        filename = f"{timestamp}_{name_base}_ai.txt"
        filepath = os.path.join(out_dir, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"===== GENERATED BY: {APP_NAME} v{CORE_VERSION} =====\n")
                f.write(f"===== SOURCE: {source} =====\n")
                f.write(f"===== MODEL: {provider} / {model} =====\n")
                if not is_batch:
                    f.write(f"===== PROMPT =====\n{prompt}\n\n")
                f.write(f"===== RESPONSE =====\n{response}")
            return filepath
        except IOError as e:
            logging.error(f"Save error: {e}")
            return None

    def _scan_directory(self, path: str, ext_filter: str, recursive: bool) -> List[str]:
        """Scans directory and collects supported files."""
        results = []
        pattern = ext_filter.lower().replace("*.* (all files)", "")
        
        for root, _, files in os.walk(path):
            if not recursive and root != path:
                continue
            for f in files:
                if pattern and not f.lower().endswith(pattern):
                    continue
                if os.path.splitext(f)[1].lower() in text_extractor.SUPPORTED_EXTRACTORS:
                    results.append(os.path.join(root, f))
        return results

    def _build_prompt(self, user_prompt: str, content: str, source_info: str) -> str:
        """Constructs the final prompt with context."""
        return (
            f"{user_prompt}\n\n"
            f"--- START OF CONTENT TO PROCESS ({source_info}) ---\n"
            f"{content}\n"
            f"--- END OF CONTENT TO PROCESS ---"
        )

    def _report_error(self, msg: str, provider: str, model: str, source_info: str = "System", is_batch: bool = False, batch_id=None):
        """Sends error to the GUI."""
        err_response = {
            "response": msg, "error": True, "input_chars": 0, "output_chars": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0, "status_message": "Error"
        }
        payload = (err_response, source_info, None, batch_id)
        target = "batch_item_result" if is_batch else "single_result"
        self.message_queue.put((target, payload))

# --- Headless Mode Methods (Inherited from GUI Logic) ---

    def _run_ai_task_sync(self, provider_key: str, model: str, prompt: str, source_info: str, original_filename: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        SYNCHRONOUS AI call for headless mode.
        Executes call, saves result, and RETURNS the result object.
        """
        provider = self.providers.get(provider_key)
        if not provider:
            msg = f"Provider unavailable: {provider_key}"
            logging.error(msg)
            return {"status": "error", "source": source_info, "error_message": msg, "result": None}

        try:
            # AI call (same logic as original _run_ai_task)
            ai_kwargs = {k: v for k, v in kwargs.items() if k in ['reasoning_effort', 'verbosity']}
            
            logging.warning(f"[HEADLESS_DIAG] Call: Provider={provider_key}, Model={model}, AI_KWARGS={ai_kwargs}, Prompt Len={len(prompt)}")

            response_dict: AIResponse = provider.get_response(model, prompt, **ai_kwargs)

            # Process result
            saved_path = None
            if not response_dict["error"]:
                # 'is_batch' and 'batch_id' are irrelevant for saving in headless,
                # but 'original_filename' is useful.
                saved_path = self._save_result(
                    provider_key, model, prompt, response_dict["response"], 
                    source_info, is_batch=False, batch_id=None, 
                    orig_filename=original_filename
                )
                
                return {
                    "status": "success",
                    "source": source_info,
                    "result": response_dict,
                    "saved_path": saved_path
                }
            else:
                # Error returned by AI provider
                return {
                    "status": "error",
                    "source": source_info,
                    "error_message": response_dict["response"],
                    "result": response_dict
                }

        except Exception as e:
            logging.error(f"Critical error during AI call (headless): {e}", exc_info=True)
            return {
                "status": "error",
                "source": source_info,
                "error_message": f"Exception occurred: {e}",
                "result": None
            }

    def _headless_batch_loop(self, items: List[str], prompt: str, provider: str, model: str, options: Dict, is_url_mode: bool) -> List[Dict[str, Any]]:
        """
        SYNCHRONOUS loop for batch processing (headless mode).
        Iterates items, runs AI calls, collects results.
        """
        results = []
        total = len(items)
        delay = options.get("delay", 1.0)
        html_opts = options.get('html_options', {})
        dynamic_opts = options.get('dynamic_options') # Added dynamic options extraction

        logging.info(f"[Headless] Starting batch processing: {total} items.")

        for i, item in enumerate(items):
            display_name = item[:70] if is_url_mode else os.path.basename(item)
            logging.info(f"[Headless] Processing {i+1}/{total}: {display_name}")

            # Extract content
            content = None
            source_info = ""
            original_name = None
            error_msg = None
            
            try:
                if is_url_mode:
                    # Update call to include dynamic_opts
                    content, source_info, error_msg = self._fetch_content_from_url(
                        item, 
                        options.get('send_raw_html', False), 
                        html_opts, 
                        dynamic_opts
                    )
                else:
                    content = text_extractor.extract_text_from_file(item, html_opts)
                    source_info = f"FILE: {display_name}"
                    original_name = display_name
                    error_msg = "Read failure" if content is None else None
            except Exception as e:
                error_msg = f"Content extraction error: {e}"
                source_info = item

            # AI call or log error
            if content:
                full_prompt = self._build_prompt(prompt, content, source_info)
                result_dict = self._run_ai_task_sync(
                    provider, model, full_prompt, source_info, 
                    original_filename=original_name, **options
                )
                results.append(result_dict)
            else:
                logging.warning(f"[Headless] Item skipped (no content): {display_name} (Error: {error_msg})")
                results.append({
                    "status": "error",
                    "source": source_info,
                    "error_message": error_msg or "Unknown content error",
                    "result": None
                })

            # Delay
            if i < total - 1 and delay > 0:
                time.sleep(delay)
                
        logging.info(f"[Headless] Batch complete. Results count: {len(results)}.")
        return results

    def process_headless(self, mode: str, prompt: str, input_data: Union[str, List[str], None], options: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Main entry point for Command Line (Headless) processing.
        Runs task synchronously and returns a list of results.
        
        Args:
            mode (str): Processing mode.
            prompt (str): System prompt.
            input_data (Union[str, List[str], None]): Files/URLs or None.
            options (Dict[str, Any]): Configuration options.

        Returns:
            List[Dict[str, Any]]: List of result dictionaries.
        """
        
        # Unpack options from headless.py
        
        # FIX: Use .pop() instead of .get()
        # This removes keys, preventing duplication when calling **options
        provider_key = options.pop("provider_key", None)
        model = options.pop("model", None)
        
        html_opts = options.get('html_options', {})
        dynamic_opts = options.get('dynamic_options') # Added extraction
        
        # Override output dir (if specified in CLI)
        if options.get("output_dir"):
            self._headless_output_dir = options.get("output_dir")

        try:
            # --- 1. Direct Input ---
            if mode == self.MODE_DIRECT:
                # headless.py handles appending input_data to prompt if needed.
                logging.info(f"[Headless] Starting {self.MODE_DIRECT}.")
                result = self._run_ai_task_sync(
                    provider_key, model, prompt, "Direct Input", **options
                )
                return [result]

            # --- 2. Single File ---
            elif mode == self.MODE_SINGLE_FILE:
                path = str(input_data)
                logging.info(f"[Headless] Starting {self.MODE_SINGLE_FILE}: {path}")
                if not path or not os.path.isfile(path):
                    return [{"status": "error", "source": path, "error_message": "Invalid file path.", "result": None}]
                
                filename = os.path.basename(path)
                content = text_extractor.extract_text_from_file(path, html_opts)
                if not content:
                    return [{"status": "error", "source": filename, "error_message": "Empty or unreadable file.", "result": None}]
                
                full_prompt = self._build_prompt(prompt, content, f"FILE: {filename}")
                result = self._run_ai_task_sync(
                    provider_key, model, full_prompt, f"FILE: {filename}", 
                    original_filename=filename, **options
                )
                return [result]

            # --- 3. Batch Files (Concatenated) ---
            elif mode == self.MODE_BATCH_FILES:
                paths = list(input_data)
                logging.info(f"[Headless] Starting {self.MODE_BATCH_FILES} (concatenated): {len(paths)} files.")
                if not paths:
                    return [{"status": "error", "source": "Batch Files", "error_message": "No input files provided.", "result": None}]

                # Same logic as _process_batch_files
                contents = []
                for path in paths:
                    text = text_extractor.extract_text_from_file(path, html_opts)
                    if text:
                        contents.append(f"--- {os.path.basename(path)} ---\n{text}")
                
                if not contents:
                    return [{"status": "error", "source": "Batch Files", "error_message": "Failed to extract content from files.", "result": None}]

                full_prompt = self._build_prompt(prompt, "\n\n".join(contents), "BATCH FILES (Concatenated)")
                result = self._run_ai_task_sync(
                    provider_key, model, full_prompt, f"Concatenated ({len(contents)} items)", 
                    original_filename="batch_concat_response", **options
                )
                return [result]

            # --- 4. Single URL ---
            elif mode == self.MODE_URL:
                url = str(input_data)
                logging.info(f"[Headless] Starting {self.MODE_URL}: {url}")
                # Updated call to include dynamic_opts
                content, info, error = self._fetch_content_from_url(
                    url, 
                    options.get('send_raw_html', False), 
                    html_opts, 
                    dynamic_opts
                )
                if error:
                    return [{"status": "error", "source": url, "error_message": error, "result": None}]

                full_prompt = self._build_prompt(prompt, content, info)
                result = self._run_ai_task_sync(
                    provider_key, model, full_prompt, info, 
                    original_filename=f"url_{os.path.basename(urlparse(url).path)}", **options
                )
                return [result]

            # --- 5. Directory (Batch) ---
            elif mode == self.MODE_BATCH_DIR:
                dir_path = str(input_data)
                logging.info(f"[Headless] Starting {self.MODE_BATCH_DIR}: {dir_path}")
                files = self._scan_directory(dir_path, options.get('file_type'), options.get('recursive'))
                if not files:
                    return [{"status": "error", "source": dir_path, "error_message": "No matching files found in directory.", "result": None}]
                
                # Call synchronous batch loop
                return self._headless_batch_loop(files, prompt, provider_key, model, options, is_url_mode=False)

            # --- 6. URL List (Batch) ---
            elif mode == self.MODE_BATCH_URL_LIST:
                urls = list(input_data)
                logging.info(f"[Headless] Starting {self.MODE_BATCH_URL_LIST}: {len(urls)} URLs.")
                if not urls:
                    return [{"status": "error", "source": "URL List", "error_message": "Empty URL list.", "result": None}]
                
                # Call synchronous batch loop
                return self._headless_batch_loop(urls, prompt, provider_key, model, options, is_url_mode=True)

            else:
                return [{"status": "error", "source": "System", "error_message": f"Unknown headless mode: {mode}", "result": None}]
    
        except Exception as e:
            logging.critical(f"[Headless] Unexpected error in process_headless: {e}", exc_info=True)
            return [{"status": "error", "source": "System", "error_message": f"Critical error: {e}", "result": None}]