# -*- coding: utf-8 -*-

"""
Sift AI - Graphical Entry Point.

This script launches the GUI application. Its responsibilities include:
1. Setting up initial (bootstrap) logging.
2. Loading Configuration and the Controller.
3. Creating the Tkinter main window (Root) and starting event loops.
4. Catching and displaying critical, unhandled errors.
"""

import tkinter as tk
from tkinter import messagebox
import logging
import sys
import traceback

# Import local modules
# Assuming the script is in the root directory
from core.version import APP_NAME, CORE_VERSION
from config_manager import ConfigManager
from core.app_controller import AppController
from gui.main_window import AppView

def _setup_bootstrap_logging():
    """
    Sets up temporary logging for the startup phase.
    This ensures we see errors even if config.json hasn't loaded yet.
    """
    try:
        logging.basicConfig(
            filename='app_pre_config.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            force=True
        )
    except Exception as e:
        # If logging fails (e.g., read-only folder), write to stderr
        print(f"CRITICAL: Bootstrap logging failed: {e}", file=sys.stderr)


def _show_critical_error(title: str, message: str):
    """
    Displays a critical error in a popup window.
    Used when the main application window does not exist or has crashed.
    """
    try:
        # Temporary, invisible root window for the messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception as e:
        # If GUI display fails, fallback to console/log
        logging.critical(f"Failed to show error window: {e}")
        print(f"FATAL ERROR ({title}): {message}", file=sys.stderr)


def main():
    """Main application launch logic."""
    
    # 1. Bootstrap Logging
    _setup_bootstrap_logging()
    logging.info(f"{APP_NAME} v{CORE_VERSION} starting...")

    try:
        # 2. Load Configuration
        # ConfigManager no longer shows popups, just sets the is_loaded flag
        config = ConfigManager(headless_mode=False)

        if not config.is_loaded:
            msg = (
                "Configuration load failed.\n"
                "Please check 'config.json' and the logs!"
            )
            logging.critical(msg)
            _show_critical_error("Configuration Error", msg)
            sys.exit(1)

        # 3. Initialize Controller
        try:
            controller = AppController(config_manager=config)
        except Exception as ctrl_err:
            msg = f"Controller initialization failed:\n{ctrl_err}"
            logging.critical(msg, exc_info=True)
            _show_critical_error("Startup Error", msg)
            sys.exit(1)

        # 4. Start GUI
        root = tk.Tk()
        
        # Instantiate Application
        # Assuming main_window.py is compatible (it is, we reviewed it)
        app = AppView(master=root, controller=controller, app_version=CORE_VERSION)

        # Handle Window Closing (Graceful shutdown)
        def on_closing():
            logging.info("Application closed by user.")
            root.destroy()
            sys.exit(0)

        root.protocol("WM_DELETE_WINDOW", on_closing)
        
        logging.info("GUI event loop starting.")
        root.mainloop()

    except Exception as e:
        # Final safety net for any unhandled exception
        err_msg = str(e)
        trace = traceback.format_exc()
        logging.critical(f"Unhandled exception in main loop: {err_msg}\n{trace}")
        
        _show_critical_error(
            "Critical Application Error",
            f"An unexpected error occurred:\n{err_msg}\n\nDetails in the log file."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()