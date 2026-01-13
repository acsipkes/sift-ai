import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import sqlite3
import time
import os
import sys
import re
import json
import copy
import subprocess
import requests
import atexit
import socket
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple, Callable
from bs4 import BeautifulSoup

try:
    from core.version import APP_NAME, DEBATE_MODULE_VERSION, DEBATE_MODULE_NAME
    from config_manager import ConfigManager
except ImportError:
    APP_NAME = "Sift AI"
    DEBATE_MODULE_NAME = "Debate Module"
    DEBATE_MODULE_VERSION = "1.0.0"
    class ConfigManager:
        def __init__(self, headless_mode=False): pass
        def get(self, key, default=None): return default

# --- 1. CONFIGURATION & CONSTANTS ---

DB_FILE = f"debate_manager_v{DEBATE_MODULE_VERSION.replace('.', '_')}.db"
SERVER_SCRIPT = "api_server.py"
# Dynamic Port Configuration
try:
    _cfg = ConfigManager(headless_mode=True)
    _srv = _cfg.get("server", {})
    API_PORT = int(_srv.get("port", 8000))
    # Client always connects to localhost, regardless of bind host (0.0.0.0)
    API_BASE_URL = f"http://localhost:{API_PORT}"
except Exception as e:
    print(f"Warning: Failed to load config ({e}), using defaults.")
    API_PORT = 8000
    API_BASE_URL = f"http://localhost:{API_PORT}"

# Timeouts and Limits
DEFAULT_API_TIMEOUT = 300  # 300 seconds (5 minutes) for reasoning models
DOSSIER_CHAR_LIMIT = 30000 # Increased limit for dossier context

PROMPTS = {
    "XML_INSTRUCTION": """
[SYSTEM INSTRUCTION: STRICT XML OUTPUT MODE]
YOU ARE A PROTOCOL-DRIVEN DEBATE AGENT.
Your output must consist SOLELY of the following two XML blocks.
Any other text (intro, explanation, markdown frames outside tags) is FORBIDDEN.

[STRUCTURE RULES]:
1. Inside <inner_monologue>, you MUST distinctively analyze:
   - STRATEGIC INTENT: What is your victory condition for this phase?
   - TACTICAL MANEUVER: What rhetorical device will you use? (e.g., Steelmanning, Pivoting, Rebuttal).
2. Inside <public_response>, deliver the speech based on these tactics.
3. Inside the <public_response> block, use Markdown formatting.

[CORRECT FORMAT EXAMPLE]:
<inner_monologue>
Here I analyze the situation, the opponent's logic, and my hidden agenda...
</inner_monologue>
<public_response>
Respected Colleagues!
Here is my public argument formatted in Markdown...
</public_response>
""",
"SCRIBE_SYSTEM": """
YOU ARE THE SCRIBE.
TASK: Update the state of the debate in valid JSON format.
[INPUT LIMIT]: {limit} characters.
[STRUCTURE]:
{{
    "summary": "Concise summary...",
    "consensus_points": ["Agreed Point 1", "Agreed Point 2"],
    "active_conflicts": [
        {{"topic": "X", "status": "OPEN/DEADLOCK/RESOLVED"}}
    ],
    "decisions": ["Decision 1"],
    "meta_notes": "..."
}}
[RULES]:
1. DO NOT remove items from the 'decisions' list unless explicitly revoked by the participants!
2. Compress text if necessary to fit the context window.
3. CURRENT MODE: {mode_name}.
""",
    "SCRIBE_FINAL": """
YOU ARE THE SCRIBE.
TASK: Generate the FINAL REPORT in Markdown format.

[STRUCTURE]:
# 1. Executive Summary
# 2. Consensus Points and Decisions
# 3. Open Questions and Divergences
# 4. Argument Map (Key arguments and counter-arguments)

[STYLE]: Objective, analytical, professional. Do NOT use JSON here, strictly plain Markdown text.
"""
}

SIFT_AI_DESCRIPTION = """
### ℹ️ About the Framework
**Sift AI** is a modular, multi-agent debate simulation framework designed to test the reasoning capabilities of Large Language Models (LLMs).
* **Protocol-Driven:** Agents (Proponent, Opponent) adhere to strict role-play protocols enforced by a separate **Moderator Agent**.
* **Chain-of-Thought:** Participants use an internal monologue (`<inner_monologue>`) to plan strategy before generating public responses (`<public_response>`).
* **State Management:** A dedicated **Scribe Agent** tracks the debate's logical state (consensus, conflicts) in JSON format to prevent circular arguments.
"""

DEBATE_PROFILES = {
    "critical": {
        "name": "🔴 Critical Challenger (Dialectical)",
        "description": "Intense logical scrutiny. Finding flaws and contradictions.",
        "mod_protocol": "[CATALYST MODE] Do not accept superficial agreement. Force agents to define their terms precisely.",
        "debater_instruction": """[DIALECTICAL DOCTRINE] 
1. STRATEGY: Expose the fragility of the opponent's premises.
2. TACTICS: Use 'Reductio ad absurdum' and demand evidence for every claim. 
3. RULE: Never agree just to be polite. Conflict is the path to truth.""",
        "private_agenda": "PRIVATE PRIORITY: Find a logical fallacy in the last statement and dismantle it publicly.",
        "phases": ["OPENING", "INTENSIVE_SCRUTINY"]
    },
    "brainstorming": {
        "name": "🟢 Think Tank (Constructive)",
        "description": "Structured solution finding and creative synthesis.",
        "mod_protocol": "[STRATEGIC ARCHITECT] Suppress criticism in the early phase. Encourage wild divergence before filtering.",
        "debater_instruction": """[CONSTRUCTIVE DOCTRINE] 
1. STRATEGY: Build, do not destroy. Synthesis is victory.
2. TACTICS: Use 'Yes, and...' thinking. Take the opponent's weak idea and upgrade it (Steelmanning).
3. RULE: Every problem raised must be accompanied by a potential solution.""",
        "private_agenda": "PRIVATE PRIORITY: Connect two seemingly unrelated ideas into a novel solution.",
        "phases": ["EXPLORATION", "DEFINITION"]
    },
    "mediator": {
        "name": "🔵 Mediator (Realist)",
        "description": "Interest alignment under asymmetry.",
        "mod_protocol": "[POWER BROKER] Identify the 'Zone of Possible Agreement' (ZOPA). Call out emotional escalation immediately.",
        "debater_instruction": """[NEGOTIATION DOCTRINE] 
1. STRATEGY: Separate the people from the problem. Maximize joint utility.
2. TACTICS: Reframe negative attacks as 'unmet needs'. Ask 'What if' questions to unlock deadlock.
3. RULE: Protect the face/reputation of all participants.""",
        "private_agenda": "PRIVATE PRIORITY: Secure a small, symbolic concession from the aggressive party to lower tension.",
        "phases": ["POSITIONS", "BARGAINING"]
    },
    "autonomous": {
        "name": "🟣 Autonomous Roundtable (No Moderator)",
        "description": "Self-organizing chaos. Individual agendas collide.",
        "mod_protocol": "[SILENT OBSERVER] STATUS: DORMANT. Only intervene if the system loops or crashes.",
        "debater_instruction": """[SOVEREIGN DOCTRINE] 
1. STRATEGY: Dominate the narrative arc. 
2. TACTICS: Interrupt (virtually) by shifting the topic entirely if the current one is losing value.
3. RULE: You are responsible for the debate's momentum. If it bores you, change it.""",
        "private_agenda": "PRIVATE PRIORITY: Establish yourself as the de facto leader of the group.",
        "phases": ["OPEN WATER", "CONVERGENCE"]
    }
}

# --- 2. DATA MODELS ---

@dataclass
class AgentConfig:
    name: str
    role: str
    provider: str
    model: str
    dossier_path: Optional[str] = None
    is_moderator: bool = False

@dataclass
class DebateSettings:
    topic: str
    rounds: int
    profile_key: str
    reasoning_effort: str
    memory_limit: int
    scribe_provider: str
    scribe_model: str

@dataclass
class AgentResponse:
    inner_monologue: str
    public_response: str
    raw_text: str

# --- 3. UTILITIES & PARSERS ---

class TextParser:
    """
    Robust utility for parsing XML-like and JSON outputs from LLMs.
    Handles missing tags, markdown fences, non-standard tags, and noisy output.
    """

    # Synonyms for tags to handle multilingual or hallucinated tag variations
    TAG_MAPPINGS = {
        "inner": [
            "inner_monologue", "inner", "gondolat", "thought", "reasoning", 
            "monologue", "internal", "analysis"
        ],
        "public": [
            "public_response", "public", "valasz", "response", "answer", 
            "speech", "argument", "reply"
        ]
    }

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Removes markdown code fences (e.g., ```xml) from the text."""
        # Remove opening fence (with or without language specifier)
        text = re.sub(r"```[a-zA-Z0-9]*\n?", "", text)
        # Remove closing fence
        text = text.replace("```", "")
        return text.strip()

    @staticmethod
    def _extract_tag_content(text: str, tags: List[str]) -> Optional[str]:
        """
        Attempts to extract content using a list of potential tag names.
        Prioritizes strict <tag>...</tag> matching, falls back to open <tag>...
        """
        tag_pattern = "|".join([re.escape(t) for t in tags])

        # 1. Strict match: Look for opening and closing tags
        # re.DOTALL ensures '.' matches newlines
        pattern_strict = rf"<\s*({tag_pattern})\s*>(.*?)<\s*/\s*\1\s*>"
        match = re.search(pattern_strict, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(2).strip()

        # 2. Loose match: Look for opening tag until the end (if closing tag is missing)
        pattern_loose = rf"<\s*({tag_pattern})\s*>(.*)"
        match = re.search(pattern_loose, text, re.DOTALL | re.IGNORECASE)
        if match:
            content = match.group(2).strip()
            # Safety: If we are extracting 'inner', ensure we don't accidentally
            # capture the 'public' tag if it follows immediately without closure.
            # We check if there's a subsequent '<' followed by text.
            if "<" in content:
                # Simple heuristic: split at the next tag start if it looks like a new block
                # This is a basic safety measure for malformed XML
                next_tag_match = re.search(r"<\s*[a-zA-Z]", content)
                if next_tag_match:
                    content = content[:next_tag_match.start()].strip()
            return content

        return None

    @staticmethod
    def extract_xml(raw_text: str) -> AgentResponse:
        """
        Extracts inner monologue and public response tolerating noise and malformed XML.
        """
        clean_text = TextParser._strip_markdown(raw_text)

        # Attempt to extract inner monologue
        inner_text = TextParser._extract_tag_content(
            clean_text, TextParser.TAG_MAPPINGS["inner"]
        )

        # Attempt to extract public response
        public_text = TextParser._extract_tag_content(
            clean_text, TextParser.TAG_MAPPINGS["public"]
        )

        # --- Fallback Logic ---

        if not inner_text:
            inner_text = "No inner monologue."

        if not public_text:
            # If no public tag found, but we have inner text, assume the rest is public
            # (unless the cleaning removed everything)
            if inner_text != "No inner monologue.":
                # Remove the inner monologue from the original text to isolate the public part
                # This is a fallback approximation
                temp_text = clean_text.replace(inner_text, "")
                # Remove the specific tags if they exist in the text
                temp_text = re.sub(r"<[^>]+>", "", temp_text).strip()
                if temp_text:
                    public_text = temp_text
            else:
                # If absolutely no tags are found, treat the whole text as public response
                # provided it doesn't look like an empty XML skeleton.
                public_text = clean_text

        # Final sanity check to ensure we return strings, not None
        if not public_text:
            public_text = raw_text  # Ultimate fallback: raw input

        return AgentResponse(
            inner_monologue=inner_text, 
            public_response=public_text, 
            raw_text=raw_text
        )

    @staticmethod
    def clean_and_parse_json(raw_text: str) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Robust JSON extraction. Finds the largest outer {} block and repairs common errors.
        """
        text = TextParser._strip_markdown(raw_text)

        # Locate the first '{' and the last '}' to ignore conversational intro/outro
        start_idx = text.find("{")
        end_idx = text.rfind("}")

        if start_idx != -1 and end_idx != -1:
            text = text[start_idx : end_idx + 1]

        try:
            return json.loads(text, strict=False), None
        except json.JSONDecodeError:
            try:
                # Common LLM error: using single quotes instead of double quotes
                fixed_text = text.replace("'", '"')
                return json.loads(fixed_text, strict=False), None
            except Exception as e:
                # Return None so the engine handles the error gracefully
                return None, f"JSON Parse Error: {str(e)}"

# --- 4. NETWORK LAYER ---

class ServerManager:
    """
    Manages the local API server process lifecycle.
    Includes port conflict detection to prevent silent failures.
    """
    
    def __init__(self, script_name: str = SERVER_SCRIPT, port: int = API_PORT):
        self.script_name = script_name
        self.port = port
        self.base_url = f"http://localhost:{port}"
        self.process = None
        self.we_started_it = False

    def is_running(self) -> bool:
        """Check if the API is responding to health checks."""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=0.5)
            return resp.status_code == 200
        except:
            return False

    def is_port_free(self) -> bool:
        """
        Check if the configured port is available using a low-level socket.
        Returns True if the port is free, False if occupied.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                # Attempt to bind to localhost on the specific port
                s.bind(("localhost", self.port))
                return True
            except OSError:
                return False

    def start(self) -> bool:
        # 1. Idempotency check: Is Sift AI already running?
        if self.is_running():
            print(f"✅ Server already active at {self.base_url}")
            return True

        # 2. Conflict check: Is the port blocked by another app?
        if not self.is_port_free():
            msg = (
                f"CRITICAL ERROR: Port {self.port} is already in use!\n\n"
                f"The server cannot start because another application is using this port.\n"
                f"Please update 'port' in 'config.json' to a different value."
            )
            print(msg)
            messagebox.showerror("Port Conflict", msg)
            return False

        # 3. Validation
        if not os.path.exists(self.script_name):
            messagebox.showerror("Critical Error", f"Missing server script: {self.script_name}")
            return False

        # 4. Process Launch
        print(f"⏳ Starting server ({self.script_name}) on port {self.port}...")
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            # Note: api_server.py reads the same config.json, so arguments are not needed.
            self.process = subprocess.Popen(
                [sys.executable, self.script_name],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                startupinfo=startupinfo
            )
            self.we_started_it = True
            
            # 5. Health Check Loop
            for i in range(15):
                time.sleep(1)
                if self.is_running():
                    print(f"✅ Server started successfully on port {self.port}!")
                    return True
            
            # 6. Timeout Handling
            self.terminate()
            messagebox.showerror("Timeout", "Server process started but API is unresponsive.\nCheck logs for details.")
            return False

        except Exception as e:
            messagebox.showerror("Startup Error", f"Failed to execute server script: {e}")
            return False

    def terminate(self):
        if self.process and self.we_started_it:
            print("🛑 Terminating server...")
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except:
                if self.process: self.process.kill()
            self.process = None

class SiftClient:
    """Handles HTTP communication with the API server with dynamic timeout support."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()

    def get_providers(self) -> Dict[str, List[str]]:
        try:
            resp = self.session.get(f"{self.base_url}/providers", timeout=2)
            if resp.status_code == 200:
                return resp.json().get("providers", {})
            return {}
        except:
            return {}

    def generate(self, provider: str, model: str, sys_prompt: str, input_text: str, reasoning: str = "medium", timeout: int = 120) -> Tuple[str, Optional[str]]:
        combined_prompt = f"ROLE/CONTEXT: {sys_prompt}\n\nDATA/INPUT: {input_text}"
        
        payload = {
            "mode": "DirectInput",
            "prompt": combined_prompt,
            "input_data": None,
            "provider": provider,
            "model": model,
            "reasoning_effort": reasoning,
            "verbosity": "medium",
            "delay": 0.0
        }
        
        try:
            resp = self.session.post(f"{self.base_url}/v1/process", json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("status") == "success" and data.get("data"):
                return data["data"][0]["result"]["response"], None
            
            return "", f"API Empty Response: {data}"
        except Exception as e:
            return "", f"API Error: {str(e)}"

# --- 5. LOGIC LAYER (CONTROLLER) ---

class DebateEngine:
    """
    Manages the debate lifecycle, state, and control flow.
    Features: Pause, Stop, Retry-on-Timeout, Dynamic Dossier Limits.
    """
    
    def __init__(self, client: SiftClient, db_path: str, log_callback: Callable, status_callback: Callable, thinking_callback: Callable[[bool], None]):
        self.client = client
        self.db_path = db_path
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.thinking_callback = thinking_callback # Callback to update GUI timer
        
        self._pause_event = threading.Event()
        self._pause_event.set() 
        self._stop_requested = False
        self._is_running = False
        
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS debate_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                round INTEGER,
                agent_name TEXT,
                msg_type TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def is_running(self) -> bool:
        return self._is_running

    def toggle_pause(self) -> str:
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.log_callback(">>> DEBATE PAUSED <<<", "SYSTEM")
            return "PAUSED"
        else:
            self._pause_event.set()
            self.log_callback(">>> DEBATE RESUMED <<<", "SYSTEM")
            return "RUNNING"

    def stop(self):
        self._stop_requested = True
        self._pause_event.set()
        self.log_callback(">>> STOP REQUESTED <<<", "ERROR")

    def run_debate(self, settings: DebateSettings, agents: List[AgentConfig]):
        """Main execution thread with retry loops."""
        self._is_running = True
        self._stop_requested = False
        self._pause_event.set()
        
        conn = None
        try:
            session_id = int(time.time())
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 1. Metadata Log
            self.log_callback(f"=== {DEBATE_MODULE_NAME} | TOPIC: {settings.topic} ===", "HEADER")
            profile = DEBATE_PROFILES[settings.profile_key]
            self.log_callback(f"MODE: {profile['name']} | MEM LIMIT: {settings.memory_limit} chars", "SYSTEM")

            meta = {
                "topic": settings.topic,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mode": profile['name'],
                "rounds": settings.rounds,
                "agents": [asdict(a) for a in agents],
                "scribe_provider": settings.scribe_provider,
                "scribe_model": settings.scribe_model,
                "reasoning_effort": settings.reasoning_effort,
                "memory_limit": settings.memory_limit
            }
            
            cursor.execute('INSERT INTO debate_logs (session_id, round, agent_name, msg_type, content) VALUES (?,?,?,?,?)',
                           (session_id, 0, "SYSTEM", "CONFIG_JSON", json.dumps(meta, ensure_ascii=False)))
            conn.commit()

            # 2. State Initialization
            current_state = {"summary": "Debate initialized.", "decisions": [], "conflicts": []}
            transcript_buffer = ""
            full_history = ""

            # 3. Main Loop
            for r in range(1, settings.rounds + 1):
                if self._stop_requested: break
                self._pause_event.wait()
                
                remaining = settings.rounds - r
                if r == 1:
                    pacing = "[PACING: OPENING] Establish core theses."
                elif remaining > 1:
                    pacing = f"[PACING: ELABORATION] {remaining} rounds left. Deepen arguments."
                elif remaining == 1:
                    pacing = "[PACING: ENDGAME] Penultimate round. Move toward conclusion."
                else:
                    pacing = "[PACING: CLOSING] FINAL ROUND. No new topics. Synthesize."

                phase_name = profile["phases"][0] if r <= settings.rounds/2 else profile["phases"][1]
                
                self.status_callback(r, settings.rounds, phase_name)
                self.log_callback(f"\n--- ROUND {r}: {phase_name} ---", "HEADER")

                # A) AGENTS (DEBATERS)
                debaters = [a for a in agents if not a.is_moderator]
                moderator = next((a for a in agents if a.is_moderator), None)

                for agent in debaters:
                    if self._stop_requested: break
                    self._pause_event.wait()

                    # 1. Dossier Handling (Smart Limit)
                    dossier_content = ""
                    if agent.dossier_path and os.path.exists(agent.dossier_path):
                        try:
                            with open(agent.dossier_path, "r", encoding="utf-8") as f:
                                raw_doc = f.read()
                                if len(raw_doc) > DOSSIER_CHAR_LIMIT:
                                    self.log_callback(f"⚠️ {agent.name} dossier truncated ({len(raw_doc)} -> {DOSSIER_CHAR_LIMIT} chars)", "SYSTEM")
                                dossier_content = f"\n<dossier>\n{raw_doc[:DOSSIER_CHAR_LIMIT]}\n</dossier>\n"
                        except: pass

                    prompt = (
                        f"TOPIC: {settings.topic}\nROUND: {r}/{settings.rounds}\n"
                        f"INSTRUCTION: {pacing}\nPHASE: {phase_name}\n"
                        f"WORLD STATE:\n{json.dumps(current_state, ensure_ascii=False)}\n"
                        f"TRANSCRIPT:\n{transcript_buffer}\n"
                        f"{dossier_content}"
                        f"DOCTRINE:\n{profile['debater_instruction']}\n"
                        f"{profile['private_agenda']}\n"
                        f"IDENTITY: {agent.name} ({agent.role})\n"
                        f"{PROMPTS['XML_INSTRUCTION']}"
                    )

                    # 2. Retry Logic for Agents
                    resp_text = ""
                    while True:
                        if self._stop_requested: break
                        self._pause_event.wait() 

                        self.log_callback(f"⏳ {agent.name} thinking... (Timeout: {DEFAULT_API_TIMEOUT}s)")
                        self.thinking_callback(True)
                        
                        resp_text, err = self.client.generate(
                            agent.provider, agent.model, agent.role, prompt, 
                            settings.reasoning_effort, timeout=DEFAULT_API_TIMEOUT
                        )
                        self.thinking_callback(False)

                        if err:
                            self.log_callback(f"❌ ERROR/TIMEOUT: {err}", "ERROR")
                            self.log_callback(">>> AUTO-PAUSE: Check connection or increase timeout, then RESUME. <<<", "SYSTEM")
                            self.toggle_pause() # Triggers pause logic, loop continues but waits at top
                        else:
                            break 

                    if self._stop_requested: break

                    response_obj = TextParser.extract_xml(resp_text)
                    
                    full_log = f"[INNER]: {response_obj.inner_monologue}\n[PUBLIC]: {response_obj.public_response}"
                    cursor.execute('INSERT INTO debate_logs (session_id, round, agent_name, msg_type, content) VALUES (?,?,?,?,?)',
                                   (session_id, r, agent.name, 'ARGUMENT', full_log))
                    conn.commit()

                    transcript_buffer += f"\n{agent.name}: {response_obj.public_response[:500]}...\n"
                    self.log_callback(f"\n--- {agent.name} ---", "HEADER")
                    if response_obj.inner_monologue != "No inner monologue.":
                        self.log_callback(f"💭 {response_obj.inner_monologue}", "INNER_MONOLOGUE")
                    self.log_callback(response_obj.public_response, "PUBLIC_RESPONSE")

                # B) MODERATOR
                if moderator and not self._stop_requested:
                    self._pause_event.wait()
                    
                    is_auto = "autonomous" in profile["name"].lower()
                    is_last = (r == settings.rounds)
                    
                    if is_auto and not is_last:
                        self.log_callback(f"\n[MODERATOR]: (Silent Observer)", "SYSTEM")
                        transcript_buffer += "\n(Moderator observing...)\n"
                    else:
                        mod_instr = (
                            "FINAL ROUND. Formally close the debate. Thank participants. Do not summarize content yet."
                            if is_last else f"Analyze the debate. {profile['mod_protocol']}"
                        )
                        
                        mod_prompt = (
                            f"TOPIC: {settings.topic}\n"
                            f"TRANSCRIPT: {transcript_buffer}\nSTATE: {json.dumps(current_state)}\n"
                            f"INSTRUCTION: {mod_instr}\n"
                            f"{PROMPTS['XML_INSTRUCTION']}"
                        )

                        # Retry Logic for Moderator
                        resp_text = ""
                        while True:
                            if self._stop_requested: break
                            self._pause_event.wait()

                            self.log_callback(f"⏳ Moderator thinking...", "SYSTEM")
                            self.thinking_callback(True)
                            resp_text, err = self.client.generate(
                                moderator.provider, moderator.model, "Moderator", mod_prompt, 
                                settings.reasoning_effort, timeout=DEFAULT_API_TIMEOUT
                            )
                            self.thinking_callback(False)

                            if err:
                                self.log_callback(f"❌ MODERATOR ERROR: {err}", "ERROR")
                                self.log_callback(">>> AUTO-PAUSE <<<", "SYSTEM")
                                self.toggle_pause()
                            else:
                                break
                        
                        if not self._stop_requested:
                            mod_resp = TextParser.extract_xml(resp_text)
                            
                            full_mod_log = f"[INNER]: {mod_resp.inner_monologue}\n[PUBLIC]: {mod_resp.public_response}"
                            cursor.execute('INSERT INTO debate_logs (session_id, round, agent_name, msg_type, content) VALUES (?,?,?,?,?)',
                                           (session_id, r, moderator.name, 'SYSTEM', full_mod_log))
                            conn.commit()

                            transcript_buffer += f"\nMODERATOR: {mod_resp.public_response[:300]}\n"
                            self.log_callback(f"\n--- MODERATOR ---", "SYSTEM")
                            if mod_resp.inner_monologue != "No inner monologue.":
                                 self.log_callback(f"💭 {mod_resp.inner_monologue}", "INNER_MONOLOGUE")
                            self.log_callback(mod_resp.public_response, "PUBLIC_RESPONSE")

                # C) SCRIBE
                if not self._stop_requested:
                    self._pause_event.wait()
                    
                    current_limit = min(4000 + (r * 1500), 50000)
                    scribe_sys = PROMPTS['SCRIBE_SYSTEM'].format(limit=current_limit, mode_name=profile['name'])
                    scribe_user = (
                        f"TOPIC: {settings.topic}\n"
                        f"PREVIOUS STATE: {json.dumps(current_state)}\n"
                        f"NEW TRANSCRIPT:\n{transcript_buffer}"
                    )
                    
                    # Retry Logic for Scribe
                    raw_scribe = ""
                    while True:
                        if self._stop_requested: break
                        self._pause_event.wait()

                        self.log_callback("📝 Scribe updating state...", "SCRIBE")
                        self.thinking_callback(True)
                        raw_scribe, err = self.client.generate(
                            settings.scribe_provider, settings.scribe_model, scribe_sys, scribe_user, 
                            "medium", timeout=DEFAULT_API_TIMEOUT
                        )
                        self.thinking_callback(False)

                        if err:
                            self.log_callback(f"❌ SCRIBE ERROR: {err}", "ERROR")
                            self.log_callback(">>> AUTO-PAUSE <<<", "SYSTEM")
                            self.toggle_pause()
                        else:
                            break

                    if not self._stop_requested:
                        state_json, parse_err = TextParser.clean_and_parse_json(raw_scribe)
                        
                        if not state_json:
                            self.log_callback(f"⚠️ Scribe Parse Error: {parse_err}", "ERROR")
                        else:
                            current_state = state_json
                            if len(current_state.get("decisions", [])) > 8: 
                                current_state["decisions"] = [current_state["decisions"][0]] + current_state["decisions"][-7:]

                        cursor.execute('INSERT INTO debate_logs (session_id, round, agent_name, msg_type, content) VALUES (?,?,?,?,?)',
                                       (session_id, r, "Scribe", 'SCRIBE', json.dumps(current_state, ensure_ascii=False)))
                        conn.commit()

                        full_history += f"\n--- ROUND {r} ---\n{transcript_buffer}"
                        transcript_buffer = ""

            # 4. FINAL REPORT
            if not self._stop_requested:
                self.log_callback("\n🏁 Generating Final Report...", "HEADER")
                final_prompt = PROMPTS['SCRIBE_FINAL']
                final_input = f"FINAL STATE: {json.dumps(current_state)}\nFULL TRANSCRIPT: {full_history}"
                
                # Retry Logic for Final Report
                report = ""
                while True:
                    if self._stop_requested: break
                    self._pause_event.wait()
                    
                    self.thinking_callback(True)
                    report, err = self.client.generate(
                        settings.scribe_provider, settings.scribe_model, final_prompt, final_input, 
                        "high", timeout=DEFAULT_API_TIMEOUT
                    )
                    self.thinking_callback(False)
                    
                    if err:
                        self.log_callback(f"❌ REPORT ERROR: {err}", "ERROR")
                        self.log_callback(">>> AUTO-PAUSE <<<", "SYSTEM")
                        self.toggle_pause()
                    else:
                        break

                if report:
                    self.log_callback("\n=== FINAL REPORT ===", "HEADER")
                    self.log_callback(report, "PUBLIC_RESPONSE")
                    cursor.execute('INSERT INTO debate_logs (session_id, round, agent_name, msg_type, content) VALUES (?,?,?,?,?)',
                        (session_id, settings.rounds + 1, "Scribe", "FINAL_REPORT", report))
                    conn.commit()

            self.log_callback("\n✅ PROCESS FINISHED.", "SYSTEM")

        except Exception as e:
            self.log_callback(f"CRITICAL ERROR: {e}", "ERROR")
            import traceback; traceback.print_exc()
        finally:
            if conn: conn.close()
            self._is_running = False
            self.status_callback(0, 0, "STOPPED")
            self.thinking_callback(False)

# --- 6. PRESENTATION LAYER (GUI) ---

class ModernDebateUI:
    """
    Modern GUI with Status Panel, Timers, and Configuration Management.
    """
    
    CONFIG_FILE = f"debate_manager_v{DEBATE_MODULE_VERSION.replace('.', '_')}.json"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} - {DEBATE_MODULE_NAME} v{DEBATE_MODULE_VERSION}")
        self.root.geometry("1450x980")
        
        self.server = ServerManager()
        if not self.server.start():
            self.root.destroy()
            return
            
        self.client = SiftClient(API_BASE_URL)
        # Initialize engine with the new callback
        self.engine = DebateEngine(
            self.client, DB_FILE, self.log, self.update_status_panel, 
            thinking_callback=self.set_thinking_state
        )
        self.available_models = {}
        
        # UI State
        self.agent_widgets = []
        self.moderator_var = tk.IntVar(value=0)
        self.status_var_round = tk.StringVar(value="Round: - / -")
        self.status_var_phase = tk.StringVar(value="Phase: IDLE")
        
        # Timer State
        self.timer_start_time = 0
        self.timer_id = None
        
        self.build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(1000, self.connect_backend)

    def build_layout(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        # 1. STATUS PANEL
        status_frame = ttk.LabelFrame(main, text=" Live Status ", padding=5)
        status_frame.pack(fill="x", pady=(0, 10))
        
        lbl_round = ttk.Label(status_frame, textvariable=self.status_var_round, font=("Consolas", 14, "bold"), foreground="#50fa7b")
        lbl_round.pack(side="left", padx=20)
        
        ttk.Separator(status_frame, orient="vertical").pack(side="left", fill="y", padx=10)
        
        lbl_phase = ttk.Label(status_frame, textvariable=self.status_var_phase, font=("Segoe UI", 12))
        lbl_phase.pack(side="left", padx=10)

        # Thinking Timer
        self.lbl_timer = ttk.Label(status_frame, text="⏱ 0s", font=("Consolas", 14), foreground="#ffb86c")
        self.lbl_timer.pack(side="right", padx=20)

        # 2. CONFIGURATION PANEL
        config_frame = ttk.LabelFrame(main, text=" 1. Configuration ", padding=5)
        config_frame.pack(fill="x", pady=5)
        
        row1 = ttk.Frame(config_frame)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="Topic:").pack(side="left")
        self.entry_topic = ttk.Entry(row1, width=50)
        self.entry_topic.insert(0, "Resolved: If an Artificial Intelligence demonstrates sentience, it should be granted the same legal rights as a human.")
        self.entry_topic.pack(side="left", padx=5, fill="x", expand=True)
        
        ttk.Label(row1, text="Rounds:").pack(side="left")
        self.spin_rounds = ttk.Spinbox(row1, from_=1, to=20, width=3)
        self.spin_rounds.set(6)
        self.spin_rounds.pack(side="left", padx=5)

        row2 = ttk.Frame(config_frame)
        row2.pack(fill="x", pady=2)
        
        ttk.Label(row2, text="Profile:").pack(side="left")
        self.cb_profile = ttk.Combobox(row2, values=list(DEBATE_PROFILES.keys()), state="readonly", width=25)
        self.cb_profile.current(0)
        self.cb_profile.pack(side="left", padx=5)
        
        ttk.Label(row2, text="Memory Limit:").pack(side="left")
        self.spin_memory = ttk.Spinbox(row2, from_=10000, to=100000, increment=5000, width=8)
        self.spin_memory.set(50000)
        self.spin_memory.pack(side="left", padx=5)

        ttk.Label(row2, text="Reasoning:").pack(side="left", padx=10)
        self.cb_reasoning = ttk.Combobox(row2, values=["low", "medium", "high"], width=8, state="readonly")
        self.cb_reasoning.current(1)
        self.cb_reasoning.pack(side="left")

        # Control Buttons
        btn_frame = ttk.Frame(config_frame)
        btn_frame.pack(side="right", padx=5)
        
        self.btn_start = ttk.Button(btn_frame, text="START ▶", command=self.start_process)
        self.btn_start.pack(side="left", padx=2)
        
        self.btn_pause = ttk.Button(btn_frame, text="PAUSE ⏸", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=2)
        
        self.btn_stop = ttk.Button(btn_frame, text="STOP ⏹", command=self.stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=2)
        
        ttk.Button(btn_frame, text="💾 Save Config", command=self.save_config).pack(side="left", padx=5)
        self.btn_export = ttk.Button(btn_frame, text="Export Log 📄", command=self.export_log, state="disabled")
        self.btn_export.pack(side="left", padx=2)

        # 3. SCRIBE & AGENTS
        mid_frame = ttk.Frame(main)
        mid_frame.pack(fill="x", pady=5)
        
        # Scribe
        scribe_grp = ttk.LabelFrame(mid_frame, text=" Scribe (Observer) ", padding=5)
        scribe_grp.pack(side="left", fill="both", padx=5)
        self.cb_scribe_prov = ttk.Combobox(scribe_grp, width=12, state="readonly")
        self.cb_scribe_prov.pack(side="left")
        self.cb_scribe_model = ttk.Combobox(scribe_grp, width=20, state="readonly")
        self.cb_scribe_model.pack(side="left", padx=5)
        self.cb_scribe_prov.bind("<<ComboboxSelected>>", lambda e: self.update_models(self.cb_scribe_prov, self.cb_scribe_model))

        # Agents
        agent_grp = ttk.LabelFrame(main, text=" Participants ", padding=5)
        agent_grp.pack(fill="x")
        
        abtn_frame = ttk.Frame(agent_grp)
        abtn_frame.pack(fill="x")
        ttk.Button(abtn_frame, text="+ Add Agent", command=self.add_agent_row).pack(side="left")
        ttk.Button(abtn_frame, text="- Remove Last", command=self.remove_agent_row).pack(side="left", padx=5)
        
        self.agents_container = ttk.Frame(agent_grp)
        self.agents_container.pack(fill="x")

        # Initial Rows
        self.add_agent_row(
            "Moderator", 
            "Facilitate the dialogue, enforce protocol, and synthesize diverging points of view.", 
            is_mod=True
        )
        self.add_agent_row(
            "Proponent", 
            "Defend the resolution with moral clarity and logical precision. Focus on benefits."
        )
        self.add_agent_row(
            "Opponent", 
            "Deconstruct the resolution by exposing risks, logical fallacies, and ethical contradictions."
        )

        # 4. LOG AREA
        log_frame = ttk.LabelFrame(main, text=" Transcript ", padding=5)
        log_frame.pack(fill="both", expand=True, pady=5)
        
        self.txt_log = scrolledtext.ScrolledText(
            log_frame, state='disabled', height=15, 
            font=("Consolas", 10), background="#282a36", foreground="#f8f8f2", insertbackground="white"
        )
        self.txt_log.pack(fill="both", expand=True)
        self.setup_tags()

    def setup_tags(self):
        self.txt_log.tag_config("HEADER", foreground="#ffb86c", font=("Arial", 10, "bold"))
        self.txt_log.tag_config("SYSTEM", foreground="#f1fa8c", background="#44475a")
        self.txt_log.tag_config("INNER_MONOLOGUE", foreground="#bd93f9", font=("Consolas", 9, "italic"), lmargin1=20)
        self.txt_log.tag_config("PUBLIC_RESPONSE", foreground="#f8f8f2")
        self.txt_log.tag_config("ERROR", foreground="#ff5555")
        self.txt_log.tag_config("SCRIBE", foreground="#50fa7b", background="#282a36")

    # --- LOGIC & HELPERS ---

    def connect_backend(self):
        providers = self.client.get_providers()
        if providers:
            self.available_models = providers
            self.log(f"✅ {APP_NAME} Core Connected. Models Loaded.", "SYSTEM")
            self.refresh_combos()
            self.load_config()
        else:
            self.log("⚠️ Backend connection failed or empty models.", "ERROR")

    def refresh_combos(self):
        provs = list(self.available_models.keys())
        self.cb_scribe_prov['values'] = provs
        if provs: self.cb_scribe_prov.current(0)
        self.update_models(self.cb_scribe_prov, self.cb_scribe_model)
        
        for w in self.agent_widgets:
            w['prov']['values'] = provs
            if provs: w['prov'].current(0)
            self.update_models(w['prov'], w['model'])

    def update_models(self, cb_prov, cb_model):
        p = cb_prov.get()
        models = self.available_models.get(p, [])
        cb_model['values'] = models
        if models: cb_model.current(0)

    def add_agent_row(self, name="Agent", role="", is_mod=False):
        if len(self.agent_widgets) >= 8: return
        idx = len(self.agent_widgets)
        row = ttk.Frame(self.agents_container)
        row.pack(fill="x", pady=2)
        
        ttk.Radiobutton(row, text="MOD", variable=self.moderator_var, value=idx).pack(side="left", padx=5)
        
        e_name = ttk.Entry(row, width=15)
        e_name.insert(0, name)
        e_name.pack(side="left", padx=2)
        
        cb_p = ttk.Combobox(row, width=10, state="readonly")
        cb_m = ttk.Combobox(row, width=22, state="readonly")
        cb_p.bind("<<ComboboxSelected>>", lambda e: self.update_models(cb_p, cb_m))
        cb_p.pack(side="left", padx=2); cb_m.pack(side="left", padx=2)
        
        e_role = ttk.Entry(row, width=25)
        e_role.insert(0, role)
        e_role.pack(side="left", fill="x", expand=True)
        
        btn_dossier = ttk.Button(row, text="📂 Dossier", command=lambda: self.browse_dossier(idx))
        btn_dossier.pack(side="left", padx=5)

        w_data = {"frame": row, "name": e_name, "prov": cb_p, "model": cb_m, "role": e_role, "dossier": None, "btn": btn_dossier}
        self.agent_widgets.append(w_data)
        
        if self.available_models:
            provs = list(self.available_models.keys())
            cb_p['values'] = provs
            if provs: cb_p.current(0)
            self.update_models(cb_p, cb_m)
        
        if is_mod: self.moderator_var.set(idx)

    def remove_agent_row(self):
        if len(self.agent_widgets) > 2:
            w = self.agent_widgets.pop()
            w["frame"].destroy()

    def browse_dossier(self, idx):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("Markdown", "*.md")])
        if path:
            # Smart Check for File Size
            try:
                size = os.path.getsize(path)
                if size > DOSSIER_CHAR_LIMIT: # Rough check
                    messagebox.showwarning(
                        "File Too Large", 
                        f"The selected file ({size} bytes) is larger than the limit ({DOSSIER_CHAR_LIMIT}).\n"
                        "The content will be truncated automatically during the debate."
                    )
            except: pass

            self.agent_widgets[idx]["dossier"] = path
            self.agent_widgets[idx]["btn"].config(text="✅ Linked")

    def log(self, text, tag=None):
        self.txt_log.config(state='normal')
        self.txt_log.insert(tk.END, text + "\n", tag)
        self.txt_log.see(tk.END)
        self.txt_log.config(state='disabled')

    def update_status_panel(self, r, max_r, phase_text):
        if phase_text == "STOPPED":
            self.status_var_round.set("Status: STOPPED")
            self.status_var_phase.set("Phase: IDLE")
            self.btn_start.config(state="normal")
            self.btn_pause.config(state="disabled")
            self.btn_stop.config(state="disabled")
            self.btn_export.config(state="normal")
            self.set_thinking_state(False)
        else:
            self.status_var_round.set(f"Round: {r} / {max_r}")
            self.status_var_phase.set(f"Phase: {phase_text}")

    # --- TIMER LOGIC ---

    def set_thinking_state(self, is_thinking: bool):
        """Callback from Engine to start/stop the GUI timer safely."""
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None

        if is_thinking:
            self.timer_start_time = time.time()
            self.lbl_timer.config(foreground="#ffb86c")
            self.update_timer()
        else:
            self.lbl_timer.config(text="⏱ 0s", foreground="#ffb86c")

    def update_timer(self):
        """Recursive function using system time delta."""
        elapsed = int(time.time() - self.timer_start_time)
        
        self.lbl_timer.config(text=f"⏱ {elapsed}s")
        
        if elapsed > DEFAULT_API_TIMEOUT * 0.8:
            self.lbl_timer.config(foreground="#ff5555")
        else:
            self.lbl_timer.config(foreground="#ffb86c")

        self.timer_id = self.root.after(1000, self.update_timer)

    # --- PROCESS CONTROL ---

    def start_process(self):
        try:
            settings = DebateSettings(
                topic=self.entry_topic.get(),
                rounds=int(self.spin_rounds.get()),
                profile_key=self.cb_profile.get(),
                reasoning_effort=self.cb_reasoning.get(),
                memory_limit=int(self.spin_memory.get()),
                scribe_provider=self.cb_scribe_prov.get(),
                scribe_model=self.cb_scribe_model.get()
            )
            
            agents = []
            mod_idx = self.moderator_var.get()
            for i, w in enumerate(self.agent_widgets):
                agents.append(AgentConfig(
                    name=w["name"].get(),
                    role=w["role"].get(),
                    provider=w["prov"].get(),
                    model=w["model"].get(),
                    dossier_path=w["dossier"],
                    is_moderator=(i == mod_idx)
                ))

            self.btn_start.config(state="disabled")
            self.btn_pause.config(state="normal", text="PAUSE ⏸")
            self.btn_stop.config(state="normal")
            self.btn_export.config(state="disabled")
            self.txt_log.config(state='normal'); self.txt_log.delete(1.0, tk.END); self.txt_log.config(state='disabled')

            threading.Thread(target=self.engine.run_debate, args=(settings, agents), daemon=True).start()
            
        except Exception as e:
            messagebox.showerror("Config Error", str(e))

    def toggle_pause(self):
        new_state = self.engine.toggle_pause()
        if new_state == "PAUSED":
            self.btn_pause.config(text="RESUME ▶")
        else:
            self.btn_pause.config(text="PAUSE ⏸")

    def stop_process(self):
        if messagebox.askyesno("Confirm Stop", "Are you sure you want to terminate the debate?"):
            self.engine.stop()
            self.btn_stop.config(state="disabled")

    # --- PERSISTENCE ---

    def save_config(self):
        data = {
            "topic": self.entry_topic.get(),
            "rounds": self.spin_rounds.get(),
            "profile": self.cb_profile.get(),
            "memory": self.spin_memory.get(),
            "scribe": {"p": self.cb_scribe_prov.get(), "m": self.cb_scribe_model.get()},
            "agents": []
        }
        mod_idx = self.moderator_var.get()
        for i, w in enumerate(self.agent_widgets):
            data["agents"].append({
                "n": w["name"].get(), "r": w["role"].get(), 
                "p": w["prov"].get(), "m": w["model"].get(), 
                "d": w["dossier"], "is_mod": (i == mod_idx)
            })
        try:
            with open(self.CONFIG_FILE, "w") as f: json.dump(data, f, indent=4)
            messagebox.showinfo("Saved", "Configuration saved successfully.")
        except Exception as e: messagebox.showerror("Error", str(e))

    def load_config(self):
        if not os.path.exists(self.CONFIG_FILE): return
        try:
            with open(self.CONFIG_FILE, "r") as f: data = json.load(f)
            self.entry_topic.delete(0, tk.END); self.entry_topic.insert(0, data.get("topic", ""))
            self.spin_rounds.set(data.get("rounds", 6))
            if data.get("profile") in DEBATE_PROFILES: self.cb_profile.set(data.get("profile"))
            self.spin_memory.set(data.get("memory", 50000))
            
            scr = data.get("scribe", {})
            if scr.get("p") in self.available_models: 
                self.cb_scribe_prov.set(scr["p"])
                self.update_models(self.cb_scribe_prov, self.cb_scribe_model)
                self.cb_scribe_model.set(scr.get("m", ""))

            # Rebuild agents
            for w in self.agent_widgets: w["frame"].destroy()
            self.agent_widgets = []
            
            for i, ag in enumerate(data.get("agents", [])):
                self.add_agent_row(ag["n"], ag["r"])
                w = self.agent_widgets[-1]
                if ag["p"] in self.available_models:
                    w["prov"].set(ag["p"])
                    self.update_models(w["prov"], w["model"])
                    w["model"].set(ag["m"])
                w["dossier"] = ag.get("d")
                if w["dossier"]: w["btn"].config(text="✅ Linked")
                if ag.get("is_mod"): self.moderator_var.set(i)
                
        except Exception as e: print(f"Load config error: {e}")

    def export_log(self):
        path = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown", "*.md")])
        if not path: return
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Retrieve latest session
        cursor.execute("SELECT MAX(session_id) FROM debate_logs")
        res = cursor.fetchone()
        if not res or res[0] is None:
            messagebox.showerror("Error", "No logs found in database.")
            conn.close()
            return
            
        sid = res[0]
        cursor.execute('SELECT round, agent_name, msg_type, content FROM debate_logs WHERE session_id=? ORDER BY id ASC', (sid,))
        rows = cursor.fetchall()
        conn.close()
        
        # Parse metadata from Round 0
        model_map = {}
        debate_meta = {}
        
        for r, name, mtype, content in rows:
            if mtype == "CONFIG_JSON":
                try:
                    debate_meta = json.loads(content)
                    for agent in debate_meta.get("agents", []):
                        role_tag = " (Moderator)" if agent.get("is_moderator") else ""
                        model_map[agent["name"]] = f"{agent['model']}{role_tag}"
                except: pass
                break
        
        scribe_model_info = debate_meta.get("scribe_model", "Unknown Model")
        reasoning_info = debate_meta.get("reasoning_effort", "Unknown")
        
        with open(path, "w", encoding="utf-8") as f:
            # --- Header ---
            f.write(f"# {APP_NAME} - Debate Transcript\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Topic:** {debate_meta.get('topic', 'N/A')}\n\n")
            
            # --- Framework Description ---
            f.write(SIFT_AI_DESCRIPTION + "\n\n")
            f.write("---\n\n")

            # --- Model Configuration Table ---
            f.write("## ⚙️ Configuration & Models\n\n")
            f.write("| Role | Agent Name | Model ID | Notes |\n")
            f.write("|---|---|---|---|\n")
            
            # Participants
            for agent_name, model_id in model_map.items():
                role = "Moderator" if "(Moderator)" in model_id else "Debater"
                clean_model = model_id.replace(" (Moderator)", "")
                f.write(f"| {role} | **{agent_name}** | `{clean_model}` | - |\n")
            
            # System Agents
            f.write(f"| System | **Scribe (Memory)** | `{scribe_model_info}` | JSON State Tracker |\n")
            f.write(f"| Setting | **Reasoning Effort** | `{reasoning_info}` | Chain-of-Thought Intensity |\n\n")
            f.write("---\n\n")

            # --- Transcript ---
            f.write("## 🗣️ Debate Transcript\n\n")
            
            for r, name, mtype, content in rows:
                agent_model_id = model_map.get(name, "")
                if not agent_model_id and name == "Scribe":
                     agent_model_id = scribe_model_info

                model_suffix = f" *({agent_model_id})*" if agent_model_id else ""

                if mtype == "ARGUMENT" or mtype == "SYSTEM":
                    if "[PUBLIC]:" in content:
                        parts = content.split("[PUBLIC]:")
                        if len(parts) > 1:
                            inner = parts[0].replace("[INNER]:", "").strip()
                            pub = parts[1].strip()
                            
                            f.write(f"### {name} (Round {r}){model_suffix}\n")
                            if inner != "No inner monologue." and inner:
                                f.write(f"<details><summary>💭 <i>Inner Monologue (Click to expand)</i></summary>\n\n> {inner}\n</details>\n\n")
                            f.write(f"{pub}\n\n---\n\n")
                        else:
                             f.write(f"### {name} (Round {r}){model_suffix}\n{content}\n\n---\n\n")

                    else:
                        if mtype != "CONFIG_JSON":
                            f.write(f"### {name}{model_suffix}\n{content}\n\n")
                            
                elif mtype == "SCRIBE":
                    f.write(f"#### 📝 Scribe Status (Round {r})\n")
                    f.write(f"> *Model: {scribe_model_info}*\n\n")
                    f.write(f"```json\n{content}\n```\n\n")
                    
                elif mtype == "FINAL_REPORT":
                    f.write(f"# 🏁 FINAL REPORT\n\n{content}\n")
        
        messagebox.showinfo("Exported", f"Log saved to {path}")

    def on_close(self):
        if messagebox.askokcancel("Exit", "Stop server and exit?"):
            self.server.terminate()
            self.root.destroy()
            sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    try:
        import sv_ttk
        sv_ttk.set_theme("dark")
    except ImportError: pass
    
    app = ModernDebateUI(root)
    root.mainloop()