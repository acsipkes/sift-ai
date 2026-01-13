# -*- coding: utf-8 -*-

"""
Command Line (Headless) Interface for Sift AI.

This script allows the application to run automatically without a GUI.
It supports input pipes, file arguments, and URLs.
"""

import argparse
import sys
import os
import logging
import json
from typing import Dict, Any, Optional, List, Union

# Ensure standard output uses UTF-8 (essential for JSON output on Windows)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# --- Bootstrap: Path Setup ---
# Ensures the script can find project modules even if not run from the root directory.
try:
    from config_manager import ConfigManager
    from core.app_controller import AppController
except ImportError:
    # Append parent directory to path if modules are not found
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    try:
        from config_manager import ConfigManager
        from core.app_controller import AppController
    except ImportError as e:
        print(f"CRITICAL ERROR: Failed to import required modules: {e}", file=sys.stderr)
        sys.exit(1)
from core.version import APP_NAME, CORE_VERSION


# Setup logging (to stderr, keeping stdout clean for JSON output)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)
log = logging.getLogger()


def _setup_arg_parser() -> argparse.ArgumentParser:
    """Defines command-line arguments."""
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} - Headless Mode",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # --- Required Settings ---
    req_grp = parser.add_argument_group('Required Arguments')
    req_grp.add_argument('-m', '--mode', required=True,
                         choices=['DirectInput', 'SingleFile', 'BatchFiles', 'URL', 'BatchDirectory', 'BatchURLList'],
                         help="Processing mode.")
    req_grp.add_argument('-p', '--provider', required=True,
                         help="AI Provider key (e.g., OpenAI, Gemini-1).")
    req_grp.add_argument('-M', '--model', required=True,
                         help="AI Model ID (e.g., gpt-4o, gemini-2.5-flash).")

    # --- Prompt (Mutually Exclusive) ---
    prompt_grp = parser.add_mutually_exclusive_group(required=True)
    prompt_grp.add_argument('-P', '--prompt', type=str, help="Prompt text provided inline.")
    prompt_grp.add_argument('-F', '--prompt-file', type=str, help="Load prompt from a file (UTF-8).")

    # --- Input / Output ---
    io_grp = parser.add_argument_group('Input / Output')
    io_grp.add_argument('-i', '--input', nargs='+',
                         help="Input file(s) or URL. If omitted, reads from stdin.")
    io_grp.add_argument('-o', '--output-dir', type=str,
                        help="Output directory (overrides config).")
    io_grp.add_argument('--format', choices=['json', 'raw'], default='json',
                        help="Output format on stdout. (Default: json)")
    
    # --- AI Fine-tuning ---
    ai_grp = parser.add_argument_group('AI Fine-tuning')
    ai_grp.add_argument('--reasoning-effort', default='medium',
                        choices=['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'disabled'],
                        help="Reasoning/Thinking effort (Gemini 3 / OpenAI o1). Includes 'xhigh' for Pro models.")
    ai_grp.add_argument('--verbosity', default='medium',
                        choices=['low', 'medium', 'high'],
                        help="Verbosity level (GPT-5.1 only). Default: medium.")

    # --- Runtime Options ---
    opt_grp = parser.add_argument_group('Runtime Options')
    opt_grp.add_argument('-d', '--delay', type=float, default=1.0,
                         help="Delay between batch items in seconds.")
    opt_grp.add_argument('-q', '--quiet', action='store_true',
                         help="Quiet mode (log only errors).")
    
    # --- Mode-Specific ---
    spec_grp = parser.add_argument_group('Mode-Specific Options')
    spec_grp.add_argument('-r', '--recursive', action='store_true', help="BatchDirectory: Scan subfolders.")
    spec_grp.add_argument('-t', '--file-type', default='*.*', help="BatchDirectory: File mask (e.g., *.pdf).")
    spec_grp.add_argument('--raw-html', action='store_true', help="URL: Send raw HTML without extraction/cleaning.")

    # --- HTML Extractor Advanced ---
    html_grp = parser.add_argument_group('HTML Extractor Advanced')
    html_grp.add_argument('--html-parser', default='html.parser', help="BS4 parser (e.g., lxml).")
    html_grp.add_argument('--html-decompose-tags', help="Comma-separated list of tags to remove.")
    html_grp.add_argument('--html-no-strip', action='store_true', help="Disable whitespace stripping.")
    html_grp.add_argument('--html-separator', default='\\n', help="Text separator between tags (e.g., '\\n', '|'). Default: \\n")

    # --- Dynamic Web Loader (Playwright) ---
    dyn_grp = parser.add_argument_group('Dynamic Web Loader (Playwright)')
    dyn_grp.add_argument('--dynamic', action='store_true', help="Enable Playwright loader (for JS/SPA sites).")
    dyn_grp.add_argument('--scroll', action='store_true', help="Enable smart scrolling (Infinite Scroll).")
    dyn_grp.add_argument('--max-scrolls', type=int, default=10, help="Maximum number of scroll steps. Default: 10.")
    dyn_grp.add_argument('--wait-selector', type=str, help="CSS selector to wait for before extraction.")
    dyn_grp.add_argument('--remove-selectors', type=str, help="Comma-separated selectors to remove from DOM (e.g. cookie banners).")

    return parser


def _resolve_input_data(args: argparse.Namespace) -> Union[str, List[str], None]:
    """
    Resolves input data from CLI arguments or STDIN.
    Returns:
        The input data (string or list), or None if no input is found.
    """
    # 1. Use direct argument (--input) if provided
    if args.input:
        if args.mode in ['SingleFile', 'BatchDirectory', 'URL']:
            if len(args.input) > 1:
                log.warning("Multiple inputs provided, but this mode only supports the first one.")
            return args.input[0]
        else:
            return args.input # BatchFiles, BatchURLList -> Keep as list

    # 2. Check STDIN (Pipe) if no argument provided
    if not sys.stdin.isatty():
        log.info("Reading data from STDIN...")
        stdin_content = sys.stdin.read().strip()
        
        if not stdin_content:
            raise ValueError("Empty input on STDIN.")
        
        # Format based on mode
        if args.mode in ['BatchFiles', 'BatchURLList']:
            # For list modes, split by lines
            return [line.strip() for line in stdin_content.splitlines() if line.strip()]
        
        # For other modes (DirectInput, SingleFile, URL), treat whole content as input
        return stdin_content

    # 3. No input found
    if args.mode != 'DirectInput':
        raise ValueError(f"Input is required for mode '{args.mode}' (--input or pipe).")
    
    return None


def run(args_list: Optional[List[str]] = None) -> int:
    """
    Main execution logic.
    
    Args:
        args_list: Argument list (for testing). If None, uses sys.argv.
    
    Returns:
        int: Exit code (0: success, 1: error).
    """
    parser = _setup_arg_parser()
    args = parser.parse_args(args_list)

    if args.quiet:
        log.setLevel(logging.ERROR)

    try:
        # 1. Load Prompt
        prompt_text = ""
        if args.prompt:
            prompt_text = args.prompt
        elif args.prompt_file:
            with open(args.prompt_file, 'r', encoding='utf-8') as f:
                prompt_text = f.read().strip()

        # 2. Resolve Input
        resolved_data = _resolve_input_data(args)
        
        # Determine controller input
        input_data_for_controller = None

        if args.mode == 'DirectInput':
            # For DirectInput, append STDIN data (if any) to the prompt
            if resolved_data is not None:
                prompt_text += "\n\n" + str(resolved_data)
            input_data_for_controller = None # Controller needs no separate input
        else:
            # For all other modes, resolved_data is the target
            input_data_for_controller = resolved_data

        # 3. Initialization
        config = ConfigManager(headless_mode=True)
        controller = AppController(config_manager=config)
        
        try:
            allowed_models = controller.get_models_for_provider(args.provider)
            
            # Check only if the provider is configured in the system
            if allowed_models: 
                if args.model not in allowed_models:
                    # Special error message if the model is missing due to safety filter
                    if "gpt-5.2-pro" in args.model:
                        log.error(f"SAFETY ERROR: Usage of '{args.model}' is disabled by the Cost Safety Switch.")
                        log.error("To enable, set ENABLE_GPT_5_2_PRO constant to True in app_controller.py.")
                    else:
                        log.error(f"ERROR: Model '{args.model}' not found or not allowed for provider '{args.provider}'.")
                        log.info(f"Available models: {', '.join(allowed_models)}")
                    return 1
        except Exception as e:
            # If validation fails (e.g. unknown provider structure), log warning but do not stop
            log.warning(f"Model validation failed: {e}")

        try:
            cli_to_controller_map = {
                "DirectInput": AppController.MODE_DIRECT,
                "SingleFile": AppController.MODE_SINGLE_FILE,
                "BatchFiles": AppController.MODE_BATCH_FILES,
                "URL": AppController.MODE_URL,
                "BatchDirectory": AppController.MODE_BATCH_DIR,
                "BatchURLList": AppController.MODE_BATCH_URL_LIST
            }
            controller_mode = cli_to_controller_map[args.mode]
        except KeyError:
            log.error(f"Internal Error: Invalid mode mapping: {args.mode}")
            return 1

        # 4. Construct Options
        
        # Unescape HTML separator (e.g. "\n" -> real newline)
        separator_cleaned = args.html_separator.replace('\\n', '\n').replace('\\t', '\t')

        # Build HTML options
        html_options_for_controller = {
            "parser": args.html_parser,
            "text_strip": not args.html_no_strip,
            "text_separator": separator_cleaned
        }
        
        if args.html_decompose_tags is not None:
            tags_to_decompose = [tag.strip() for tag in args.html_decompose_tags.split(',') if tag.strip()]
            html_options_for_controller["decompose_tags"] = tags_to_decompose
        
        # Build Dynamic Loader (Playwright) Options
        dynamic_options_for_controller = {
            "enabled": args.dynamic,
            "scroll": args.scroll,
            "max_scrolls": args.max_scrolls,
            "wait_selector": args.wait_selector
        }
        
        if args.remove_selectors is not None:
             selectors = [s.strip() for s in args.remove_selectors.split(',') if s.strip()]
             dynamic_options_for_controller["remove_selectors"] = selectors

        options = {
            "provider_key": args.provider,
            "model": args.model,
            "mode": controller_mode,
            "output_dir": args.output_dir,
            "delay": args.delay,
            "recursive": args.recursive,
            "file_type": args.file_type,
            "send_raw_html": args.raw_html,
            "html_options": html_options_for_controller,
            "dynamic_options": dynamic_options_for_controller, # Passed to controller -> WebLoader
            "reasoning_effort": args.reasoning_effort,
            "verbosity": args.verbosity
        }

        # 5. Process
        log.info(f"{APP_NAME} v{CORE_VERSION} starting: {args.mode} | {args.provider}/{args.model}")
        results = controller.process_headless(
            mode=controller_mode,
            prompt=prompt_text,
            input_data=input_data_for_controller,
            options=options
        )

        # 6. Output
        if args.format == 'json':
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            # Raw output
            texts = [r.get('result', {}).get('response', '') for r in results if r.get('status') == 'success']
            print("\n\n---\n\n".join(texts))

        # Check for errors in batch
        if any(r.get('status') == 'error' for r in results):
            log.error("One or more errors occurred during processing.")
            return 1

        return 0

    except ValueError as ve:
        log.error(f"Configuration/Input Error: {ve}")
        return 1
    except Exception as e:
        log.critical(f"Unexpected Error: {e}", exc_info=not args.quiet)
        return 1


def main():
    """Wrapper for command line execution."""
    sys.exit(run())


if __name__ == "__main__":
    main()