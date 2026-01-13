# -*- coding: utf-8 -*-

"""
This module implements the User Interface (GUI) via the AppView class.
The View layer is responsible for rendering the interface and forwarding
user interactions to the Controller.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import queue
import os
import logging
from typing import Dict, Any, List, Optional, Union, Tuple

# Import refactored modules
from core.app_controller import AppController
from core.text_extractor import SUPPORTED_EXTRACTORS
from core.version import APP_NAME

# Constants
DEFAULT_HTML_PARSER = 'html.parser'
DEFAULT_HTML_DECOMPOSE_TAGS = ["script", "style", "meta", "link", "header", "footer", "nav", "aside"]
DEFAULT_HTML_TEXT_SEPARATOR = '\n'
DEFAULT_HTML_TEXT_STRIP = True

class AppView:
    """
    Main application window and GUI components.
    Delegates all logical operations to the injected controller object.
    """
    def __init__(self, master: tk.Tk, controller: AppController, app_version: str) -> None:
        """
        Initialize the main application window.

        Args:
            master (tk.Tk): The root Tkinter window.
            controller (AppController): The controller instance managing application logic.
            app_version (str): The version string of the application.
        """
        self.master = master
        self.controller = controller
        self.message_queue = controller.message_queue
        self.app_version = app_version
        
        self.selected_files: List[str] = []
        self.selected_directory: Optional[str] = None
        
        # State variables for batch processing
        self.batch_processing_active: bool = False
        self.batch_total_items: int = 0
        self.batch_processed_items: int = 0
        self.batch_output_log: List[str] = []

        self.master.title(f"{APP_NAME} {self.app_version}")
        
        self._setup_style()
        self._setup_widgets()
        self._setup_layout()
        self._bind_events()
        
        self._initialize_ui_state()
        
        # Start the background message poller
        self.master.after(100, self.check_queue)

    def _setup_style(self) -> None:
        """
        Configures the visual theme of the application.
        Attempts to apply the 'sv_ttk' theme if available, otherwise falls back to default.
        """
        try:
            # sv-ttk provides a modern look and feel (Windows 11 style)
            import sv_ttk
            sv_ttk.set_theme("light")
            logging.info("sv-ttk theme applied.")
        except ImportError:
            logging.warning("sv-ttk library not found, using default theme.")

    def _setup_widgets(self) -> None:
        """Creates and initializes all GUI components (widgets)."""
        # --- Main Frames ---
        self.prompt_frame = ttk.Frame(self.master, padding="5")
        self.input_options_frame = ttk.Labelframe(self.master, text="Input & Mode", padding="5")
        self.ai_options_frame = ttk.Labelframe(self.master, text="AI Settings", padding="5")
        
        # --- HTML Extractor Settings ---
        self.html_settings_frame = ttk.Labelframe(self.master, text="HTML Extractor Settings", padding="5")
        self.html_params_inner_frame = ttk.Frame(self.html_settings_frame, padding="5")
        
        self.process_frame = ttk.Frame(self.master, padding="10")
        self.progress_frame = ttk.Frame(self.master, padding="5 0 5 5")
        self.output_frame = ttk.Labelframe(self.master, text="AI Response", padding="5")
        self.status_frame = ttk.Frame(self.master, padding="5")

        # --- Prompt ---
        self.prompt_label = ttk.Label(self.prompt_frame, text="Prompt:")
        self.prompt_text = scrolledtext.ScrolledText(self.prompt_frame, wrap=tk.WORD, height=5, relief=tk.SOLID, borderwidth=1)

        # --- Input Modes ---
        # Note: Values must match constants in AppController
        self.input_mode = tk.StringVar(value="Direct Input")
        self.mode_radio_frame_outer = ttk.Frame(self.input_options_frame)
        self.mode_radio_row1 = ttk.Frame(self.mode_radio_frame_outer)
        self.mode_radio_row2 = ttk.Frame(self.mode_radio_frame_outer)

        self.mode_direct_radio = ttk.Radiobutton(self.mode_radio_row1, text="Direct Input", variable=self.input_mode, value="Direct Input")
        self.mode_single_radio = ttk.Radiobutton(self.mode_radio_row1, text="Single File", variable=self.input_mode, value="Single File")
        self.mode_batch_radio = ttk.Radiobutton(self.mode_radio_row1, text="Batch Files (Concatenate)", variable=self.input_mode, value="Batch Files")
        self.mode_url_radio = ttk.Radiobutton(self.mode_radio_row1, text="URL", variable=self.input_mode, value="URL")
        self.mode_batch_dir_radio = ttk.Radiobutton(self.mode_radio_row2, text="Directory (Batch)", variable=self.input_mode, value="Batch Directory")
        self.mode_batch_urllist_radio = ttk.Radiobutton(self.mode_radio_row2, text="URL List", variable=self.input_mode, value="Batch URL List")
        
        # --- File/Directory Selection ---
        self.file_path_label = ttk.Label(self.input_options_frame, text="Source File:")
        self.file_path_entry = ttk.Entry(self.input_options_frame, width=60, state='readonly')
        self.browse_button = ttk.Button(self.input_options_frame, text="Browse...")

        # --- Batch Directory Options ---
        self.batch_dir_options_frame = ttk.Frame(self.input_options_frame)
        self.file_type_label = ttk.Label(self.batch_dir_options_frame, text="File Type:")
        supported_ext_list = sorted([ext for ext in SUPPORTED_EXTRACTORS.keys() if ext])
        combobox_values = ["*.* (All Files)"] + supported_ext_list
        self.file_type_combobox = ttk.Combobox(self.batch_dir_options_frame, values=combobox_values, width=20, state="readonly")
        self.file_type_combobox.set(combobox_values[0])
        self.recursive_scan_var = tk.BooleanVar(value=False)
        self.recursive_scan_checkbutton = ttk.Checkbutton(self.batch_dir_options_frame, text="Scan Subfolders (Recursive)", variable=self.recursive_scan_var)

        # --- URL Options ---
        self.url_input_frame = ttk.Frame(self.input_options_frame)
        self.url_label = ttk.Label(self.url_input_frame, text="Source URL:")
        self.url_entry = ttk.Entry(self.url_input_frame, width=60)
        self.url_list_label = ttk.Label(self.url_input_frame, text="URL List (one per line):")
        self.url_list_text = scrolledtext.ScrolledText(self.url_input_frame, wrap=tk.WORD, height=4, relief=tk.SOLID, borderwidth=1)
        
        self.send_raw_html_var = tk.BooleanVar(value=False)
        self.send_raw_html_checkbutton = ttk.Checkbutton(self.url_input_frame, text="Send Raw HTML (No cleaning)", variable=self.send_raw_html_var)

        # [NEW] Dynamic Loader Options
        # Defaults are pulled from config manager or set to False
        default_use_dynamic = self.controller.config_manager.get("defaults", {}).get("use_dynamic_loader", False)
        self.use_dynamic_var = tk.BooleanVar(value=default_use_dynamic)
        self.use_dynamic_checkbutton = ttk.Checkbutton(self.url_input_frame, text="Use Dynamic Loader (Playwright/JS)", variable=self.use_dynamic_var)
        
        self.use_scroll_var = tk.BooleanVar(value=False)
        self.use_scroll_checkbutton = ttk.Checkbutton(self.url_input_frame, text="Enable Scrolling (Infinite Scroll)", variable=self.use_scroll_var)

        # --- AI Settings ---
        self.provider_label = ttk.Label(self.ai_options_frame, text="AI Provider:")
        self.provider_combobox = ttk.Combobox(self.ai_options_frame, state="readonly", width=25)
        self.model_label = ttk.Label(self.ai_options_frame, text="Model:")
        self.model_combobox = ttk.Combobox(self.ai_options_frame, state="readonly", width=30)
        
        self.reasoning_effort_label = ttk.Label(self.ai_options_frame, text="Reasoning Effort:")
        self.reasoning_effort_var = tk.StringVar(value=self.controller.config_manager.get('defaults', {}).get('reasoning_effort', 'medium'))
        
        # Includes "none" for GPT-5.1 / Responses API support
        self.reasoning_effort_combobox = ttk.Combobox(self.ai_options_frame, textvariable=self.reasoning_effort_var, values=["none", "minimal", "low", "medium", "high"], state="disabled", width=15)
        
        self.verbosity_label = ttk.Label(self.ai_options_frame, text="Verbosity:")
        self.verbosity_var = tk.StringVar(value="medium")
        self.verbosity_combobox = ttk.Combobox(self.ai_options_frame, textvariable=self.verbosity_var, values=["low", "medium", "high"], state="disabled", width=15)
        
        self.delay_label = ttk.Label(self.ai_options_frame, text="Delay (sec):")
        self.delay_var = tk.DoubleVar(value=self.controller.config_manager.get('defaults', {}).get('batch_delay_seconds', 1.0))
        self.delay_spinbox = ttk.Spinbox(self.ai_options_frame, from_=0.0, to=300.0, increment=0.5, textvariable=self.delay_var, width=8, format="%.1f")

        # --- HTML Extractor Widgets ---
        self.show_html_params_var = tk.BooleanVar(value=False)
        self.show_html_params_checkbutton = ttk.Checkbutton(self.html_settings_frame, text="Show Advanced HTML Extractor Settings", variable=self.show_html_params_var)
        
        self.html_parser_label = ttk.Label(self.html_params_inner_frame, text="Parser:")
        self.html_parser_entry = ttk.Entry(self.html_params_inner_frame, width=20)
        self.html_parser_entry.insert(0, DEFAULT_HTML_PARSER)

        self.html_decompose_tags_label = ttk.Label(self.html_params_inner_frame, text="Tags to remove (comma sep.):")
        self.html_decompose_tags_entry = ttk.Entry(self.html_params_inner_frame)
        self.html_decompose_tags_entry.insert(0, ",".join(DEFAULT_HTML_DECOMPOSE_TAGS))

        self.html_text_separator_label = ttk.Label(self.html_params_inner_frame, text="Text Separator (e.g. \\n):")
        self.html_text_separator_entry = ttk.Entry(self.html_params_inner_frame, width=10)
        self.html_text_separator_entry.insert(0, DEFAULT_HTML_TEXT_SEPARATOR.replace('\n', '\\n').replace('\t', '\\t'))

        self.html_text_strip_var = tk.BooleanVar(value=DEFAULT_HTML_TEXT_STRIP)
        self.html_text_strip_checkbutton = ttk.Checkbutton(self.html_params_inner_frame, text="Strip whitespace", variable=self.html_text_strip_var)
        
        # --- Processing, Output, Status ---
        self.process_button = ttk.Button(self.process_frame, text="Start Processing")
        self.progress_bar = ttk.Progressbar(self.progress_frame, orient='horizontal', mode='indeterminate')
        self.output_text = scrolledtext.ScrolledText(self.output_frame, wrap=tk.WORD, height=25, relief=tk.SOLID, borderwidth=1, state="disabled")
        self.status_label = ttk.Label(self.status_frame, text="Ready.")

    def _setup_layout(self) -> None:
        """Organizes widgets using grid layout manager."""
        self.master.columnconfigure(0, weight=1)
        
        self.prompt_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=2)
        self.prompt_frame.columnconfigure(1, weight=1)
        self.prompt_label.grid(row=0, column=0, sticky="nw", padx=(0, 5))
        self.prompt_text.grid(row=0, column=1, sticky="ew")

        self.input_options_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        self.input_options_frame.columnconfigure(1, weight=1)
        
        self.mode_radio_frame_outer.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.mode_radio_row1.pack(fill=tk.X)
        self.mode_radio_row2.pack(fill=tk.X)
        self.mode_direct_radio.pack(side=tk.LEFT, padx=5, pady=2)
        self.mode_single_radio.pack(side=tk.LEFT, padx=5, pady=2)
        self.mode_batch_radio.pack(side=tk.LEFT, padx=5, pady=2)
        self.mode_url_radio.pack(side=tk.LEFT, padx=5, pady=2)
        self.mode_batch_dir_radio.pack(side=tk.LEFT, padx=5, pady=2)
        self.mode_batch_urllist_radio.pack(side=tk.LEFT, padx=5, pady=2)
        
        self.file_path_label.grid(row=1, column=0, sticky="w", pady=(5, 2))
        self.file_path_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5), pady=(5, 2))
        self.browse_button.grid(row=1, column=2, sticky="w", pady=(5, 2))

        self.batch_dir_options_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.file_type_label.grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.file_type_combobox.grid(row=0, column=1, sticky="w")
        self.recursive_scan_checkbutton.grid(row=0, column=2, sticky="w", padx=(10, 0))

        # --- URL Input Frame Layout Update ---
        self.url_input_frame.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.url_input_frame.columnconfigure(1, weight=1)
        
        self.url_label.grid(row=0, column=0, sticky="w")
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(0,5))
        
        self.url_list_label.grid(row=1, column=0, sticky="nw")
        self.url_list_text.grid(row=1, column=1, sticky="ew", padx=(0,5))
        
        # Checkbuttons row
        self.send_raw_html_checkbutton.grid(row=2, column=1, sticky="w", pady=(2,0))
        self.use_dynamic_checkbutton.grid(row=3, column=1, sticky="w", pady=(2,0))
        self.use_scroll_checkbutton.grid(row=4, column=1, sticky="w", pady=(2,0))
        
        self.ai_options_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        self.ai_options_frame.columnconfigure(1, weight=1)
        self.ai_options_frame.columnconfigure(3, weight=1)
        self.provider_label.grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.provider_combobox.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.model_label.grid(row=0, column=2, sticky="w", padx=(0, 5))
        self.model_combobox.grid(row=0, column=3, columnspan=2, sticky="ew")
        
        # --- HTML Extractor Layout ---
        self.html_settings_frame.grid(row=3, column=0, sticky="ew", padx=5, pady=5)
        self.show_html_params_checkbutton.pack(side=tk.TOP, anchor=tk.NW, pady=(0,5))
        
        self.html_params_inner_frame.columnconfigure(1, weight=1)
        self.html_parser_label.grid(row=0, column=0, sticky="w", padx=(0,5), pady=2)
        self.html_parser_entry.grid(row=0, column=1, sticky="ew", pady=2)
        self.html_decompose_tags_label.grid(row=1, column=0, sticky="w", padx=(0,5), pady=2)
        self.html_decompose_tags_entry.grid(row=1, column=1, sticky="ew", pady=2)
        self.html_text_separator_label.grid(row=2, column=0, sticky="w", padx=(0,5), pady=2)
        self.html_text_separator_entry.grid(row=2, column=1, sticky="w", pady=2)
        self.html_text_strip_checkbutton.grid(row=3, column=0, columnspan=2, sticky="w", pady=2)

        self.process_frame.grid(row=4, column=0, sticky="ew")
        self.process_button.pack()

        self.progress_frame.grid(row=5, column=0, sticky="ew")
        self.progress_frame.columnconfigure(0, weight=1)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=5)

        self.master.rowconfigure(6, weight=1)
        self.output_frame.grid(row=6, column=0, sticky="nsew", padx=5, pady=(0, 5))
        self.output_frame.columnconfigure(0, weight=1)
        self.output_frame.rowconfigure(0, weight=1)
        self.output_text.grid(row=0, column=0, sticky="nsew")

        self.status_frame.grid(row=7, column=0, sticky="ew")
        self.status_frame.columnconfigure(0, weight=1)
        self.status_label.grid(row=0, column=0, sticky="ew")

    def _bind_events(self) -> None:
        """Binds event handlers to widgets."""
        self.process_button.config(command=self.handle_start_processing)
        self.provider_combobox.bind("<<ComboboxSelected>>", self.update_models)
        self.model_combobox.bind("<<ComboboxSelected>>", self.update_dynamic_ai_options)
        self.browse_button.config(command=self.browse_file_or_directory)
        
        self.show_html_params_checkbutton.config(command=self._toggle_html_params_visibility)

        for radio in [self.mode_direct_radio, self.mode_single_radio, self.mode_batch_radio,
                      self.mode_url_radio, self.mode_batch_dir_radio, self.mode_batch_urllist_radio]:
            radio.config(command=self.update_gui_for_mode_change)

    def _initialize_ui_state(self) -> None:
        """Sets initial UI state based on data from the controller."""
        self.update_providers()
        self.update_gui_for_mode_change()
        self._toggle_html_params_visibility()
    
    def _toggle_html_params_visibility(self) -> None:
        """
        Toggles the visibility of the advanced HTML extraction settings frame
        based on the checkbox state.
        """
        if self.show_html_params_var.get():
            self.html_params_inner_frame.pack(side=tk.TOP, fill=tk.X, expand=True, pady=5, padx=5)
        else:
            self.html_params_inner_frame.pack_forget()
            
    def _get_html_extractor_options(self) -> Dict[str, Any]:
        """
        Retrieves the configured parameters for HTML text extraction from the GUI.

        Returns:
            Dict[str, Any]: A dictionary containing parsing options (parser, tags, separators).
        """
        options: Dict[str, Any] = {}
        if self.show_html_params_var.get():
            parser_val = self.html_parser_entry.get().strip()
            if parser_val:
                options['parser'] = parser_val
            
            tags_str = self.html_decompose_tags_entry.get().strip()
            options['decompose_tags'] = [tag.strip() for tag in tags_str.split(',') if tag.strip()] if tags_str else []
            
            sep_val_str = self.html_text_separator_entry.get()
            if sep_val_str:
                if sep_val_str == "\\n":
                    options['text_separator'] = '\n'
                elif sep_val_str == "\\t":
                    options['text_separator'] = '\t'
                else:
                    options['text_separator'] = sep_val_str
            
            options['text_strip'] = self.html_text_strip_var.get()
            logging.info(f"Using custom HTML extractor options from GUI: {options}")
        else:
            logging.info("HTML extractor: detailed params checkbox not checked, using defaults.")
        
        return options

    def update_providers(self) -> None:
        """Populates the provider combobox with available AI providers from the controller."""
        providers = self.controller.get_available_providers()
        self.provider_combobox['values'] = providers
        default_provider = self.controller.config_manager.get("defaults", {}).get("provider")
        
        if providers:
            if default_provider and default_provider in providers:
                self.provider_combobox.set(default_provider)
            else:
                self.provider_combobox.set(providers[0])
            self.update_models()
        else:
            self.provider_combobox.set("No providers available")
            self.model_combobox.set("")
            self.model_combobox['values'] = []
            self.process_button.config(state="disabled")

    def update_models(self, event: Optional[tk.Event] = None) -> None:
        """
        Populates the model list based on the selected provider.
        Uses the controller to filter available models.
        
        Args:
            event (Optional[tk.Event]): The event that triggered this method, if any.
        """
        provider = self.provider_combobox.get()
        if not provider or provider == "No providers available": return

        # Call the controller's filter method to get relevant models
        model_names = self.controller.get_models_for_provider(provider)
            
        self.model_combobox['values'] = model_names
        default_model = self.controller.config_manager.get("defaults", {}).get("model")
        
        if model_names:
            current_provider_is_default = self.provider_combobox.get() == self.controller.config_manager.get("defaults", {}).get("provider")
            if current_provider_is_default and default_model in model_names:
                self.model_combobox.set(default_model)
            else:
                self.model_combobox.set(model_names[0])
            self.process_button.config(state="normal")
        else:
            self.model_combobox.set("")
            self.process_button.config(state="disabled")
        
        self.update_dynamic_ai_options()

    def update_dynamic_ai_options(self, event: Optional[tk.Event] = None) -> None:
        """
        Updates visibility and values of AI-specific options (Reasoning, Verbosity, Delay)
        based on the selected provider, model, and input mode.

        Handles dynamic population of reasoning levels (e.g., adding 'xhigh' for Pro models).

        Args:
            event (Optional[tk.Event]): The event that triggered this method, if any.
        """
        provider = self.provider_combobox.get()
        model = self.model_combobox.get()
        mode = self.input_mode.get()

        # --- 1. Reasoning Effort (Thinking Level / Budget) ---
        show_reasoning = False
        # Default base values for reasoning effort
        reasoning_values = ["none", "minimal", "low", "medium", "high"]
        
        # Label text default
        label_text = "Reasoning Effort:"

        # Logic for OpenAI (GPT-5, o1, o3)
        if provider == "OpenAI" and (model.startswith('o') or model.startswith('gpt-5')):
            show_reasoning = True
            if "pro" in model:
                reasoning_values.append("xhigh")
        
        # Logic for Gemini 3 Series (Thinking Levels)
        elif "gemini-3" in model:
            show_reasoning = True
            label_text = "Thinking Level:"
            # Gemini 3 supports standard levels, 'xhigh' usually maps to High but kept for consistency
        
        # Logic for Gemini 2.5 Series (Thinking Budget)
        elif "gemini-2.5" in model:
            # Lite models do not support thinking
            if "lite" in model:
                show_reasoning = False
            else:
                show_reasoning = True
                label_text = "Thinking Budget:"
                # Pro models support higher token budgets
                if "pro" in model:
                    reasoning_values.append("xhigh")

        if show_reasoning:
            self.reasoning_effort_label.config(text=label_text)
            self.reasoning_effort_label.grid(row=1, column=0, sticky="w", padx=(0, 5), pady=5)
            self.reasoning_effort_combobox.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=5)
            self.reasoning_effort_combobox.config(state="readonly")
            
            # Update combobox values dynamically
            self.reasoning_effort_combobox.config(values=reasoning_values)
            
            # Reset selection if current value is invalid for the new model
            current_val = self.reasoning_effort_var.get()
            if current_val not in reasoning_values:
                self.reasoning_effort_var.set("medium")
        else:
            self.reasoning_effort_label.grid_remove()
            self.reasoning_effort_combobox.grid_remove()

        # --- 2. Verbosity (Only for GPT-5.1 / 5.2) ---
        if provider == "OpenAI" and "gpt-5" in model: 
            self.verbosity_label.grid(row=1, column=2, sticky="w", padx=(10, 5), pady=5)
            self.verbosity_combobox.grid(row=1, column=3, sticky="ew", padx=(0, 10), pady=5)
            self.verbosity_combobox.config(state="readonly")
        else:
            self.verbosity_label.grid_remove()
            self.verbosity_combobox.grid_remove()
            
        # --- 3. Delay (For Batch modes) ---
        if mode in ["Batch Directory", "Batch URL List"]:
            self.delay_label.grid(row=1, column=4, sticky="w", padx=(10, 5), pady=5)
            self.delay_spinbox.grid(row=1, column=5, sticky="ew", pady=5)
            self.delay_spinbox.config(state="normal")
        else:
            self.delay_label.grid_remove()
            self.delay_spinbox.grid_remove()

    def update_gui_for_mode_change(self) -> None:
        """Updates the state and visibility of widgets based on the currently selected input mode."""
        mode = self.input_mode.get()
        
        # Hide all specific fields first
        self.file_path_label.grid_remove()
        self.file_path_entry.grid_remove()
        self.browse_button.grid_remove()
        self.batch_dir_options_frame.grid_remove()
        self.url_input_frame.grid_remove()
        
        # Reset input field
        self.file_path_entry.config(state='normal')
        self.file_path_entry.delete(0, tk.END)
        self.file_path_entry.config(state='readonly')

        if mode == "Single File":
            self.file_path_label.config(text="Source File:")
            self.browse_button.config(text="Browse...")
            self.file_path_label.grid()
            self.file_path_entry.grid()
            self.browse_button.grid()
        elif mode == "Batch Files":
            self.file_path_label.config(text="Selected Files:")
            self.browse_button.config(text="Browse...")
            self.file_path_label.grid()
            self.file_path_entry.grid()
            self.browse_button.grid()
        elif mode == "Batch Directory":
            self.file_path_label.config(text="Source Directory:")
            self.browse_button.config(text="Browse...")
            self.file_path_label.grid()
            self.file_path_entry.grid()
            self.browse_button.grid()
            self.batch_dir_options_frame.grid()
        elif mode == "URL":
            self.url_input_frame.grid()
            self.url_label.grid()
            self.url_entry.grid()
            self.url_list_label.grid_remove()
            self.url_list_text.grid_remove()
            
            # Show checkboxes for URL mode
            self.send_raw_html_checkbutton.grid()
            self.use_dynamic_checkbutton.grid()
            self.use_scroll_checkbutton.grid()
            
        elif mode == "Batch URL List":
            self.url_input_frame.grid()
            self.url_label.grid_remove()
            self.url_entry.grid_remove()
            self.url_list_label.grid()
            self.url_list_text.grid()
            
            # Show checkboxes for URL List mode
            self.send_raw_html_checkbutton.grid()
            self.use_dynamic_checkbutton.grid()
            self.use_scroll_checkbutton.grid()
        
        self.update_dynamic_ai_options()
    
    def browse_file_or_directory(self) -> None:
        """
        Opens a file or directory selection dialog based on the current input mode
        and updates the file path entry.
        """
        mode = self.input_mode.get()
        path: Union[str, Tuple[str, ...], None] = None
        
        if mode == "Single File": 
            path = filedialog.askopenfilename(parent=self.master, title="Select a file")
        elif mode == "Batch Files": 
            path = filedialog.askopenfilenames(parent=self.master, title="Select one or more files")
        elif mode == "Batch Directory": 
            path = filedialog.askdirectory(parent=self.master, title="Select a directory")
        
        if not path: return

        self.file_path_entry.config(state='normal')
        self.file_path_entry.delete(0, tk.END)
        
        if isinstance(path, str):
            self.file_path_entry.insert(0, path)
            if mode == "Single File": self.selected_files = [path]
            if mode == "Batch Directory": self.selected_directory = path
        elif isinstance(path, tuple): # askopenfilenames returns a tuple
            self.file_path_entry.insert(0, f"{len(path)} files selected")
            self.selected_files = list(path)
            
        self.file_path_entry.config(state='readonly')

    def handle_start_processing(self) -> None:
        """
        Validates input and initiates the processing workflow via the controller.
        Collects all UI options and passes them to the backend.
        """
        prompt = self.prompt_text.get(1.0, tk.END).strip()
        if not prompt:
            messagebox.showwarning("Warning", "Please enter a prompt!", parent=self.master)
            return
        
        # Collect Dynamic Loader options
        dynamic_options = {
            "enabled": self.use_dynamic_var.get(),
            "scroll": self.use_scroll_var.get()
        }
        
        options: Dict[str, Any] = {
            "file_path": self.selected_files[0] if self.input_mode.get() == 'Single File' and self.selected_files else None,
            "file_paths": self.selected_files if self.input_mode.get() == 'Batch Files' else [],
            "dir_path": self.selected_directory,
            "file_type": self.file_type_combobox.get(),
            "recursive": self.recursive_scan_var.get(),
            "url": self.url_entry.get().strip(),
            "urls": [u.strip() for u in self.url_list_text.get(1.0, tk.END).strip().splitlines() if u.strip()],
            "html_options": self._get_html_extractor_options(),
            "send_raw_html": self.send_raw_html_var.get(),
            "dynamic_options": dynamic_options, # Pass dynamic options to controller
            "reasoning_effort": self.reasoning_effort_var.get(),
            "verbosity": self.verbosity_var.get(),
            "delay": self.delay_var.get()
        }
        
        self._disable_gui_elements()
        self.controller.start_processing(
            mode=self.input_mode.get(),
            prompt=prompt,
            provider_key=self.provider_combobox.get(),
            model=self.model_combobox.get(),
            options=options
        )

    def check_queue(self) -> None:
        """
        Periodically polls the message queue for updates from background threads.
        Handles status updates, processing results, and batch progress.
        """
        try:
            while True:
                message_type, data = self.message_queue.get_nowait()

                if message_type == "status":
                    self._update_status(data)
                
                elif message_type == "single_result":
                    result_dict, source_info, saved_filepath, _ = data
                    self._update_output_display(result_dict, source_info, saved_filepath)
                    self._enable_gui_elements()

                elif message_type == "batch_start":
                    self.batch_processing_active = True
                    self.batch_total_items = data
                    self.batch_processed_items = 0
                    self.batch_output_log = [f"Batch processing started: {data} items.\n"]
                    self.output_frame.config(text="Batch Log")
                    self._update_batch_log_display()

                elif message_type == "batch_progress":
                    current, total, name = data
                    self.progress_bar.config(mode='determinate', maximum=total, value=current + 1)
                    self._update_status(f"Batch: {current+1}/{total} - {name}")

                elif message_type == "batch_item_result":
                    self.batch_processed_items += 1
                    result_dict, source_info, saved_filepath, _ = data
                    is_error = result_dict.get("error", False)
                    log_entry = ""
                    if is_error:
                        log_entry = f"ERROR - {source_info}: {result_dict.get('response', '')[:100]}..."
                    else:
                        log_entry = f"SUCCESS - {source_info}: Saved to: {os.path.basename(saved_filepath) if saved_filepath else 'Save Failed'}"
                    self.batch_output_log.append(log_entry)
                    self._update_batch_log_display()
                    
                    if self.batch_processed_items >= self.batch_total_items:
                        self.message_queue.put(("batch_complete", None))

                elif message_type == "batch_complete":
                     self._update_status(f"Batch complete. Processed: {self.batch_processed_items}/{self.batch_total_items}.")
                     self.batch_processing_active = False
                     self._enable_gui_elements()

                self.message_queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.master.after(100, self.check_queue)
            
    def _update_status(self, message: str) -> None:
        """
        Updates the text of the status bar label.

        Args:
            message (str): The new status message to display.
        """
        self.status_label.config(text=message)

    def _update_output_display(self, result_dict: Dict[str, Any], source_info: str, saved_filepath: Optional[str]) -> None:
        """
        Updates the main output text area with the processing result.

        Args:
            result_dict (Dict[str, Any]): Dictionary containing response data or error info.
            source_info (str): Identifier for the source (e.g., filename or URL).
            saved_filepath (Optional[str]): Path where the result was saved, if applicable.
        """
        self.output_frame.config(text="AI Response")
        self.output_text.config(state="normal")
        self.output_text.delete(1.0, tk.END)
        
        response = result_dict.get("response", "<empty response>")
        is_error = result_dict.get("error", False)
        
        self.output_text.insert(tk.END, response)
        
        if is_error:
            self.output_text.tag_add("error", "1.0", tk.END)
            self.output_text.tag_config("error", foreground="red")
            self._update_status(f"Error: {source_info}")
        elif saved_filepath:
            filename = os.path.basename(saved_filepath)
            self._update_status(f"Done. Saved: {filename}")
        else:
             self._update_status("Done, but save failed.")
            
        self.output_text.config(state="disabled")

    def _update_batch_log_display(self) -> None:
        """Refreshes the output text area to show the current batch processing log."""
        self.output_text.config(state="normal")
        self.output_text.delete(1.0, tk.END)
        self.output_text.insert(tk.END, "\n".join(self.batch_output_log))
        self.output_text.see(tk.END)
        self.output_text.config(state="disabled")

    def _disable_gui_elements(self) -> None:
        """Disables interactive controls (buttons, inputs) during processing."""
        mode = self.input_mode.get()
        if "Batch" in mode:
             self.progress_bar.config(mode='determinate', value=0)
        else:
            self.progress_bar.config(mode='indeterminate')
            self.progress_bar.start(10)
        
        for child in self.master.winfo_children():
            if isinstance(child, (ttk.Frame, ttk.Labelframe)):
                for widget in child.winfo_children():
                    # Disable widgets in inner HTML frame too
                    if widget == self.html_params_inner_frame:
                         for sub_widget in widget.winfo_children():
                              if isinstance(sub_widget, (ttk.Entry, ttk.Checkbutton)):
                                   try: sub_widget.config(state="disabled")
                                   except (tk.TclError, AttributeError): pass
                    
                    if isinstance(widget, (ttk.Button, ttk.Radiobutton, ttk.Combobox, ttk.Entry, ttk.Checkbutton, ttk.Spinbox, scrolledtext.ScrolledText)):
                        try: widget.config(state="disabled")
                        except (tk.TclError, AttributeError): pass
        self.process_button.config(state="disabled")


    def _enable_gui_elements(self) -> None:
        """Enables interactive controls after processing finishes."""
        self.progress_bar.stop()
        self.progress_bar.config(value=0, mode='determinate')
        
        for child in self.master.winfo_children():
            if isinstance(child, (ttk.Frame, ttk.Labelframe)):
                for widget in child.winfo_children():
                     # Enable widgets in inner HTML frame
                     if widget == self.html_params_inner_frame:
                          for sub_widget in widget.winfo_children():
                              if isinstance(sub_widget, (ttk.Entry, ttk.Checkbutton)):
                                   try: sub_widget.config(state="normal")
                                   except (tk.TclError, AttributeError): pass

                     if isinstance(widget, (ttk.Button, ttk.Radiobutton, ttk.Checkbutton, scrolledtext.ScrolledText)):
                        try: widget.config(state="normal")
                        except (tk.TclError, AttributeError): pass
        
        self.provider_combobox.config(state="readonly")
        if self.model_combobox.get(): self.model_combobox.config(state="readonly")
        self.file_path_entry.config(state="readonly")
        self.file_type_combobox.config(state="readonly")

        # Update GUI state based on current mode and dynamic options
        self.update_gui_for_mode_change()
        self.update_dynamic_ai_options()