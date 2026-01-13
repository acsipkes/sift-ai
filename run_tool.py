# -*- coding: utf-8 -*-

"""
Standalone Tool Runner for Sift AI.

This script serves as a Command Line Interface (CLI) to execute specific
data collection tools (e.g., NeighborhoodCollector, WikivoyageCollector)
and immediately process the results using the AI core engine.
"""

import argparse
import sys
import logging
import os
from typing import Dict, Type, Any, Optional

# --- Bootstrap Path for Core Modules ---
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Core Imports
from config_manager import ConfigManager
from core.app_controller import AppController
from core.version import APP_NAME

# Tool Imports
# In a stricter environment, we would import a BaseTool abstract class here for typing.
from tools.neighborhood_collector import NeighborhoodCollector
from tools.wikivoyage_collector import WikivoyageCollector

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Tool Registry ---
# Maps CLI argument names to Tool Classes.
TOOL_REGISTRY: Dict[str, Type[Any]] = {
    "neighborhood": NeighborhoodCollector,
    "travel_guide": WikivoyageCollector,
}


def main() -> None:
    """
    Main entry point for the tool runner.
    Parses arguments, executes the tool, and triggers AI processing.
    """
    # 1. Parse Arguments
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Tool Runner - Modular Architecture")
    
    # Universal arguments
    parser.add_argument('--tool', required=True, choices=list(TOOL_REGISTRY.keys()),
                        help="Select the tool to run.")
    parser.add_argument('--input', required=True, 
                        help="Input data for the tool (e.g., Address, Ticker, URL).")
    parser.add_argument('--context', required=True, 
                        help="User context or profile for the AI analysis.")
    
    # AI Settings
    parser.add_argument('--provider', default="Gemini-1", help="AI Provider ID")
    parser.add_argument('--model', default="gemini-2.0-flash", help="AI Model ID")
    
    args = parser.parse_args()

    # 2. Instantiate the Selected Tool
    tool_class: Type[Any] = TOOL_REGISTRY[args.tool]
    
    # Assuming tools have a parameterless constructor or handle init internally
    tool_instance: Any = tool_class()
    
    logging.info(f"--- Starting Tool: {tool_instance.get_description()} ---")
    logging.info(f"Input Data: {args.input}")

    # 3. Run the Tool (Data Collection)
    collected_data: str = ""
    try:
        logging.info("Running external data collection...")
        # POLYMORPHISM IN ACTION: We call .run() regardless of which tool it is.
        result = tool_instance.run(args.input)
        # Ensure result is string
        collected_data = str(result)
    except Exception as e:
        logging.critical(f"Tool execution failed: {e}")
        sys.exit(1)

    if collected_data.startswith("ERROR"):
        logging.error(collected_data)
        sys.exit(1)

    # 4. Construct the System Prompt (RAG / Data Injection)
    system_prompt: str = (
        f"ROLE: You are an expert analyst powered by real-time data.\n"
        f"CONTEXT/PROFILE: {args.context}\n"
        f"TASK: Analyze the provided data based on the context.\n"
        f"DATA SOURCE: {tool_instance.get_description()}\n"
        f"INSTRUCTION: Be critical, factual, and concise.\n\n"
        f"--- COLLECTED REAL-TIME DATA ---\n{collected_data}"
    )

    # 5. Initialize Core Engine
    config = ConfigManager(headless_mode=True)
    
    # Note: AppController might raise exceptions during init
    try:
        controller = AppController(config_manager=config)
    except Exception as e:
        logging.critical(f"Failed to initialize AppController: {e}")
        sys.exit(1)

    # 6. Generate AI Analysis
    logging.info("Generating AI analysis...")
    
    # Prepare options dictionary
    opts: Dict[str, Any] = {
        "provider_key": args.provider,
        "model": args.model,
        "reasoning_effort": "medium"
    }

    results: Optional[Any] = controller.process_headless(
        mode=AppController.MODE_DIRECT, # Injecting the prompt directly
        prompt=system_prompt,
        input_data=None, 
        options=opts
    )

    # 7. Output Result
    if results and isinstance(results, list) and len(results) > 0:
        first_result = results[0]
        if first_result.get('status') == 'success':
            print("\n" + "="*50)
            # Safe access to nested dictionary
            response_text = first_result.get('result', {}).get('response', 'No response text found.')
            print(response_text)
            print("="*50 + "\n")
            logging.info(f"Analysis saved to: {first_result.get('saved_path', 'Not saved')}")
        else:
            logging.error(f"AI Processing returned error status: {first_result.get('status')}")
    else:
        logging.error("AI Processing failed or returned invalid format.")


if __name__ == "__main__":
    main()