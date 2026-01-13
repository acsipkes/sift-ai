# -*- coding: utf-8 -*-

"""
HTTP API Server for Sift AI.
"""

import logging
import sys
import uvicorn
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# --- Project Module Imports ---
try:
    from config_manager import ConfigManager
    from core.app_controller import AppController
except ImportError as e:
    print(f"CRITICAL ERROR: Failed to import required modules: {e}", file=sys.stderr)
    sys.exit(1)
from core.version import APP_NAME, CORE_VERSION

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(f"{APP_NAME}_API")


# --- Data Models (Pydantic) ---

class HTMLOptions(BaseModel):
    """Configuration for HTML text extraction."""
    parser: str = Field("html.parser", description="The parser to use (e.g., 'html.parser').")
    decompose_tags: List[str] = Field(
        default_factory=lambda: ["script", "style", "meta", "link"],
        description="List of HTML tags to remove from the DOM."
    )
    text_separator: str = Field("\n", description="Separator character for extracted text.")
    text_strip: bool = Field(True, description="Whether to strip whitespace from extracted text.")


class DynamicOptions(BaseModel):
    """Configuration for Playwright-based dynamic web loading."""
    enabled: bool = Field(False, description="Enable Playwright for dynamic content.")
    scroll: bool = Field(False, description="Enable infinite scrolling simulation.")
    max_scrolls: int = Field(10, description="Maximum number of scroll steps.")
    wait_selector: Optional[str] = Field(None, description="CSS selector to wait for before extraction.")
    remove_selectors: List[str] = Field(
        default_factory=list, 
        description="CSS selectors to remove from DOM via JavaScript."
    )


class AgentRequest(BaseModel):
    """Request model for the AI processing engine."""
    
    # Core Arguments
    mode: str = Field(..., description="Processing mode: 'DirectInput', 'URL', 'SingleFile', 'BatchURLList', 'BatchDirectory'.")
    prompt: str = Field(..., description="The system prompt or instruction for the AI.")
    input_data: Union[str, List[str], None] = Field(None, description="Input content (text, file path, URL, or list of these).")
    
    # AI Configuration
    provider: str = Field("Gemini-1", description="AI Provider Key (as defined in config).")
    model: str = Field("gemini-2.5-flash", description="AI Model ID.")
    
    # Tuning Parameters
    reasoning_effort: str = Field("medium", description="Thinking effort (e.g., for Gemini 3 or o1 models).")
    verbosity: str = Field("medium", description="Verbosity level (e.g., for GPT-5.1).")
    
    # Execution Options
    delay: float = Field(1.0, description="Delay between batch items in seconds.")
    send_raw_html: bool = Field(False, description="If True, sends raw HTML instead of cleaned text.")
    output_dir: Optional[str] = Field(None, description="Override the default output directory.")

    # Batch Directory Options
    recursive: bool = Field(False, description="BatchDirectory: Scan subfolders recursively.")
    file_type: str = Field("*.*", description="BatchDirectory: File mask (e.g., *.pdf). Default: *.*")
    
    # Advanced Options
    html_options: Optional[HTMLOptions] = None
    dynamic_options: Optional[DynamicOptions] = None


# --- Global State ---
class AppContext:
    """Holds the application singleton instances."""
    controller: Optional[AppController] = None
    config: Optional[ConfigManager] = None

app_context = AppContext()


# --- Lifecycle Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the startup and shutdown lifecycle of the AI engine."""
    logger.info(f"--- STARTUP: Initializing {APP_NAME} Engine v{CORE_VERSION} ---")
    try:
        config = ConfigManager(headless_mode=True)
        if not config.is_loaded:
            logger.error("Configuration failed to load.")
            sys.exit(1)
            
        controller = AppController(config_manager=config)
        app_context.config = config
        app_context.controller = controller
        
        providers = controller.get_available_providers()
        logger.info(f"AI Engine Ready. Active Providers: {providers}")
        yield
    except Exception as e:
        logger.critical(f"Startup Failure: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("--- SHUTDOWN: Cleaning up resources ---")


# --- FastAPI Application ---
app = FastAPI(title=f"{APP_NAME} API", version=CORE_VERSION, lifespan=lifespan)


# --- Endpoints ---

@app.get("/health")
def health_check() -> Dict[str, str]:
    """Checks if the system is initialized and active."""
    if not app_context.controller:
        raise HTTPException(status_code=503, detail="System not initialized")
    return {"status": "active"}


@app.get("/providers")
def get_providers() -> Dict[str, Dict[str, List[str]]]:
    """Retrieves available AI providers and their supported models."""
    if not app_context.controller:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    # 1. Retrieve provider names
    provider_names = app_context.controller.get_available_providers()
    
    # 2. Retrieve models for each provider
    full_schema: Dict[str, List[str]] = {}
    for p_name in provider_names:
        models = app_context.controller.get_models_for_provider(p_name)
        full_schema[p_name] = models
        
    return {"providers": full_schema}


@app.post("/v1/process")
def process_request(req: AgentRequest) -> Dict[str, Any]:
    """
    Main processing endpoint.
    Accepts processing requests for text, files, or URLs and routes them to the AI engine.
    """
    if not app_context.controller:
        raise HTTPException(status_code=503, detail="System not initialized")

    logger.info(f"Incoming Request: Mode={req.mode} | AI={req.provider}/{req.model}")

    # Map external API mode strings to internal AppController constants
    mode_mapping = {
        "DirectInput": "Direct Input",
        "SingleFile": "Single File",
        "BatchFiles": "Batch Files",
        "URL": "URL",
        "BatchDirectory": "Batch Directory",
        "BatchURLList": "Batch URL List"
    }

    internal_mode = mode_mapping.get(req.mode)
    if not internal_mode:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid mode: {req.mode}. Valid options: {list(mode_mapping.keys())}"
        )

    # Prepare options dictionary for the controller
    html_opts = req.html_options.model_dump() if req.html_options else {}
    dyn_opts = req.dynamic_options.model_dump() if req.dynamic_options else {}

    options_dict = {
        "provider_key": req.provider,
        "model": req.model,
        "reasoning_effort": req.reasoning_effort,
        "verbosity": req.verbosity,
        "delay": req.delay,
        "output_dir": req.output_dir,
        "send_raw_html": req.send_raw_html,
        "recursive": req.recursive,
        "file_type": req.file_type,
        "html_options": html_opts,
        "dynamic_options": dyn_opts
    }

    try:
        results = app_context.controller.process_headless(
            mode=internal_mode,
            prompt=req.prompt,
            input_data=req.input_data,
            options=options_dict
        )
        
        if not results:
            return {"status": "empty", "data": []}
            
        return {"status": "success", "data": results}

    except ValueError as ve:
        logger.error(f"Validation Error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.critical(f"Internal Processing Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal Logic Error: {str(e)}")


if __name__ == "__main__":
    try:
        # Load configuration for server settings
        startup_config = ConfigManager(headless_mode=True)
        server_conf = startup_config.get("server", {})
        
        host = server_conf.get("host", "0.0.0.0")
        port = int(server_conf.get("port", 8000))
        
        logger.info(f"Starting server on {host}:{port}...")
        
        uvicorn.run(app, host=host, port=port)
        
    except Exception as e:
        logger.critical(f"Server startup failed: {e}")
        sys.exit(1)
