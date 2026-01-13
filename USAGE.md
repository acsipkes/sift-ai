# 📘 Sift AI v1.0.0 – User Guide & Best Practices

This document provides a comprehensive guide to operating Sift AI, with a special focus on the **Debate Module** and strategic model selection for optimal results.

---

## ⚙️ 1. Setup & Configuration

Before running the application, ensure your API keys are configured.
Sift AI supports multiple providers. For the best experience, we recommend having keys for at least **OpenAI** and **Google Gemini**, as mixing models yields superior results.

* **Config File:** `config.json` (created automatically on first run).
* **Environment Variables:** You can also set keys via `.env` file (e.g., `OPENAI_API_KEY`, `GEMINI_1_API_KEY`).

---

## 🖥️ 2. Desktop Analyzer (GUI)

*Command:* `python main.py`

The main window is designed for document processing and extraction.

### Input Modes

* **Direct Input:** Paste text directly for quick summarization.
* **Single File / Batch Files:** Process PDF, DOCX, or RTF documents.
* **URL / Batch URL:** Scrape web content.
* **Tip:** Check **"Use Dynamic Loader"** if the website is complex (uses JavaScript/React). This engages the Playwright engine to scroll and render the page before extraction.



### AI Settings

* **Reasoning Effort:** Controls how much time the model spends "thinking" (CoT).
* *Low/Medium:* Good for summaries.
* *High/X-High:* Essential for complex legal analysis (available on Gemini 3 and OpenAI o1).



---

## ⚖️ 3. Debate Module (The Core Engine)

*Command:* `python debate.py`

This module simulates a multi-agent dialogue. The quality of the debate depends heavily on **Model Orchestration** (which model is assigned to which role).

### 🧠 Model Selection Strategy (Pro Tips)

Based on extensive testing, using a single model for all roles creates an "echo chamber." For diverse and high-quality outcomes, follow these architectural guidelines:

#### A. The Moderator (The Judge)

* **Requirement:** High reasoning capability, strict rule adherence, neutrality.
* **Recommended Models:** `gpt-5.2-pro`, `gemini-3-pro`, `claude-opus-4-1`.
* **Why:** The Moderator must synthesize conflicting arguments and enforce the protocol. Weak models tend to be too passive or forget the rules.

#### B. The Scribe (The Memory)

* **Requirement:** Massive context window to hold the entire transcript.
* **Recommended Models:** **Google Gemini 3 Preview** (Flash or Pro).
* **Why:** The Scribe needs to read the entire history of the debate (potentially 50k+ tokens) to generate accurate JSON states. Gemini's context window (2M+ tokens) makes it uniquely qualified for this role.

#### C. The Participants (Debaters)

The best model depends on the **Profile** you choose:

**1. For Critical Debates (Adversarial)**

* *Goal:* Find flaws, argue logic, win the point.
* *Recommendation:* **Top-tier Reasoning Models** (`o1`, `gpt-5.2`, `deepseek-reasoner`).
* *Reasoning:* These models are stubborn and logically rigorous. They simulate a "clash of titans" effectively.

**2. For Think Tanks (Constructive/Creative)**

* *Goal:* Brainstorming, synthesis, "Yes, and..." approach.
* *Recommendation:* **Mid-tier / Fast Models** (`gemini-2.5-flash`, `gpt-5-mini`, `claude-haiku`).
* *Reasoning:* Top-tier models often have "big egos"—they tend to lecture rather than collaborate. Mid-tier models are often more flexible, obedient to the persona, and willing to build upon another agent's idea rather than deconstructing it.

### 📂 Using Dossiers (Knowledge Injection)

You can upload a specific text file (e.g., a contract, a court ruling, or a philosophical essay) for each agent.

* **Usage:** Click the `📂 Dossier` button next to an agent.
* **Effect:** The agent will treat this file as its "core knowledge" or "instructions" and cite it during the debate.
* **Limit:** The system handles up to 30,000 characters per dossier automatically.

---

## ⌨️ 4. Headless Mode (Automation)

*Command:* `python headless.py`

Ideal for integrating Sift AI into other workflows.

**Example: Quick Web Summary**

```bash
python headless.py --mode URL --provider OpenAI --model gpt-5-mini --input "https://techcrunch.com" --prompt "List the top 3 headlines"

```

**Example: Batch Process a Folder of PDFs**

```bash
python headless.py --mode BatchDirectory --provider Gemini-1 --model gemini-2.5-flash --input "./my_documents" --prompt "Extract all dates and amounts" --output-dir "./results"

```

---

## ❓ Troubleshooting

* **"Server failed to start":** The Debate Module requires port `8000` to be free. Check if another instance is running.
* **"Playwright Error":** If web loading fails, ensure you ran `playwright install chromium`.
* **Empty Responses:** If using a "Thinking" model (e.g., o1), ensure the timeout in `config.json` is set high enough (default is 300s).

---
