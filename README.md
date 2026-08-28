# 🏗️ BuildSense: Intelligent Civil Engineering Assistant & Construction Automation Engine

> **Author:** Rahul Mandal  
> **Internship:** Infosys Springboard Virtual Internship  
> **Project:** BuildSense — Multi-Agent AI Decision Support System  

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Framework-Flask_3.1-green.svg)](https://flask.palletsprojects.com/)
[![LangChain](https://img.shields.io/badge/Orchestration-LangChain-orange.svg)](https://www.langchain.com/)
[![Prometheus](https://img.shields.io/badge/Monitoring-Prometheus-red.svg)](https://prometheus.io/)
[![Grafana](https://img.shields.io/badge/Telemetry-Grafana-orange.svg)](https://grafana.com/)
[![Docker](https://img.shields.io/badge/Deployment-Docker-blue.svg)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/Pytest-47%20Passed-brightgreen.svg)]()

---

## 📌 Executive Summary

**BuildSense** is an enterprise-grade multi-agent AI system designed to automate civil engineering decision-making, architectural blueprint analysis, fire safety compliance audits, cost estimation, construction scheduling, and interior design planning.

By combining **deterministic backend processing in Python** with **LLM-powered multi-agent orchestration (Gemini 2.0/3.5 & Groq GPT-OSS)**, BuildSense bridges the gap between modern generative AI and strict, error-free engineering standards.

---

## 📑 Table of Contents
1. [Key Features & What Makes This Unique](#-key-features--what-makes-this-unique)
2. [Real-World Applications & Use Cases](#-real-world-applications--use-cases)
3. [System Architecture & Agent Flowchart](#-system-architecture--agent-flowchart)
4. [Multi-Agent System Roster](#-multi-agent-system-roster)
5. [Enterprise Tool Integrations](#-enterprise-tool-integrations)
6. [4-Layer Memory System Architecture](#-4-layer-memory-system-architecture)
7. [📊 End-to-End Monitoring Stack (Prometheus & Grafana)](#-end-to-end-monitoring-stack-prometheus--grafana)
8. [📁 Repository Structure](#-repository-structure)
9. [🚀 Quickstart & Installation Guide](#-quickstart--installation-guide)
10. [🧪 Simulation Mode (Testing Without API Keys)](#-testing-without-an-api-key-simulation-mode)
11. [🛡️ Key Security & Engineering Design Decisions](#-key-security--engineering-design-decisions)
12. [⚖️ Advantages & Current Limitations](#-advantages--current-limitations)
13. [🔮 Future Roadmap](#-future-roadmap)

---

## 💡 Key Features & What Makes This Unique

* **Hybrid Intelligence (AI + Deterministic Python Math):** Offloads spatial calculations, material quantities, and BOQ budgeting to pure Python calculation engines. This eliminates LLM mathematical hallucinations, saves tokens, and guarantees 100% calculation accuracy.
* **Inter-Agent Shared Memory Bus:** Specialized agents collaborate by passing structured JSON context payloads without re-querying AI models unnecessarily.
* **4-Layer Memory Hierarchy:** Combines short-term runtime context with an inter-agent bus, persistent multi-tenant SQLite database isolation, and an offline JSON knowledge store.
* **Dynamic Tool Registry with Resilient Retries:** Agents dynamically fetch live material prices, OpenWeatherMap advisories, and NBC 2016 building code standards with 3-attempt exponential backoff and offline simulation fallbacks.
* **Multi-Key Round-Robin Rotation:** Built-in automatic API key rotation across key pools for Groq and Gemini models to eliminate rate limits (`429 Too Many Requests`).
* **Declarative Grafana & Prometheus Telemetry:** Full observability stack tracking HTTP request volume, route latency percentiles (`p50`, `p95`, `p99`), in-flight requests, and AI model execution durations.

---

## 🏢 Real-World Applications & Use Cases

* **Construction Project Planning:** Rapid cost estimation and milestone timeline generation for contractors, civil engineers, and project managers.
* **Safety & Regulatory Compliance Audits:** Automated fire safety compliance checks (stairwell clearances, corridor widths, emergency exit counts) against **National Building Code (NBC 2016)** prior to municipal submission.
* **Architectural Blueprint Analysis:** Automated visual extraction of total area, room layouts, corridor dimensions, and exit coordinates from raw floor plan images.
* **Resource & Workforce Management:** Optimizing labor allocation, calculating minimum required workers based on project duration, and assessing site safety risks driven by weather data.
* **Interior Design Studio:** Custom room-by-room interior recommendations specifying furniture placement, color palettes, flooring, wall textures, and ambient lighting based on designated architectural styles.

---

## 📐 System Architecture & Agent Flowchart

```
                   USER / WEB DASHBOARD
                            │
               "Can we finish Phase 2 within
                ₹15L while NBC compliant?"
                            │
                            ▼
               ┌─────────────────────────┐
               │   Flask REST API Server │
               └────────────┬────────────┘
                            │
                            ▼
               ┌─────────────────────────┐
               │    Coordinator Agent    │
               │   (Query Orchestrator)  │
               └────────────┬────────────┘
                            │ Routes & Orchestrates
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   Blueprint   │   │     Cost      │   │  NBC Safety   │
│ Vision Agent  │   │Estimator Agent│   │Compliance Agt │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │ Shared Context Payload
                            ▼
               ┌─────────────────────────┐
               │ Inter-Agent Memory Bus  │
               └────────────┬────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        ▼                                       ▼
┌───────────────┐                       ┌───────────────┐
│ Scheduling &  │                       │   Interior    │
│ Workforce Agt │                       │ Design Studio │
└───────┬───────┘                       └───────┬───────┘
        │                                       │
        └───────────────────┬───────────────────┘
                            │ Synthesizes Decision + Audit Trail
                            ▼
               ┌─────────────────────────┐
               │ Final Recommendation    │
               │ + BOQ + NBC Verdict     │
               └─────────────────────────┘
```



---

## 🤖 Multi-Agent System Roster

| Agent | Responsibility & Role |
|---|---|
| **Coordinator Agent** | Parses user query → routes tasks → resolves cross-agent conflicts → synthesizes final verdict |
| **Blueprint Vision Agent** | Multi-modal vision analysis extracting rooms, corridors, exits, and spatial dimensions |
| **Cost Estimation Agent** | Generates itemized Bill of Quantities (BOQ) and total cost estimates in INR |
| **Code Compliance Agent** | Audits architectural metrics against National Building Code (NBC 2016 Part 4/Part 3) |
| **Scheduling Agent** | Constructs phase-by-phase construction timelines with critical path analysis |
| **Workforce Agent** | Matches required trades to local contractor directory and evaluates labor constraints |
| **Interior Design Agent** | Evaluates spatial function and proposes room-specific styling, lighting, and furniture plans |

---

## 🛠️ Enterprise Tool Integrations

The agents leverage a centralized `ToolRegistry` to execute actions and fetch external data:

* **Material Price Lookup**: Regional unit cost database for accurate BOQ estimations.
* **Weather Risk API**: Live site risk analysis using OpenWeatherMap for outdoor phase planning.
* **NBC 2016 Code Lookup**: Offline building code database for fire safety and setback rules.
* **Calendar Engine**: Dynamic construction timeline generation and event persistence.
* **JSON Report Exporter**: Exports synthesized verdicts and execution traces to structured JSON reports.

---

## 🧠 4-Layer Memory System Architecture

1. **Short-Term In-Memory Context:** Tracks active session conversations and transient state within the Flask runtime during user interactions.
2. **Inter-Agent Shared Memory Bus:** Allows specialized agents to exchange structured JSON context without redundant LLM calls.
3. **Persistent User Store (SQLite):** Ensures multi-tenant isolation, user authentication, persistent chat session history, and blueprint record tracking per user account.
4. **Agent Knowledge Store (JSON):** Contains static civil engineering standards, NBC 2016 rules, default material rates, and cost lookup tables for offline fallback execution.

---

## 📊 End-to-End Monitoring Stack (Prometheus & Grafana)

BuildSense includes a production-grade monitoring stack instrumented via `prometheus-client` to track API health, request throughput, route latency percentiles, and AI agent completion metrics.

### Option A: Declarative Docker Setup (Recommended)

1. **Start Grafana & Prometheus auto-provisioned services:**
   ```bash
   docker compose up -d
   ```

2. **Access Dashboard UI:**
   - **Grafana Dashboard:** [http://localhost:3000](http://localhost:3000) (User: `admin` / Password: `admin`)
   - **Prometheus UI:** [http://localhost:9090](http://localhost:9090)
   - **Flask Metrics Endpoint:** [http://localhost:5000/metrics](http://localhost:5000/metrics)
   *The dashboard and Prometheus datasource load automatically from `grafana/dashboards/`.*

3. **Stop Services:**
   ```bash
   docker compose down
   ```

### Option B: Windows Standalone Executables (No Docker Required)
For Windows systems without Docker or hardware virtualization:

```powershell
# 1. Automated download & extraction of Prometheus & Grafana binaries
.\monitoring\setup_windows_monitoring.ps1

# 2. Launch background monitoring executables
.\monitoring\start_monitoring.ps1
```

### Exported Metrics Overview
* `http_requests_total`: Counter tracking request volume by HTTP method, route, and status code (2xx, 4xx, 5xx).
* `http_request_duration_seconds`: Histogram measuring route latency percentiles (`p50`, `p95`, `p99`).
* `active_requests_in_flight`: Gauge tracking active concurrent user requests per endpoint.
* `ai_agent_calls_total`: Counter tracking total executions across Gemini and Groq AI agents by status and key alias.
* `ai_agent_call_duration_seconds`: Histogram measuring execution latency for multi-agent completion pipelines.

---

## 📁 Repository Structure

```text
BuildSense/
├── agents/
│   ├── __init__.py           # Package exports
│   ├── config.py             # API key rotation & LLM initialization
│   ├── coordinator.py        # Coordinator + multi-agent synthesis logic
│   ├── blueprint.py          # Blueprint Vision Agent
│   ├── cost_estimation.py    # Cost Estimation Agent (BOQ Engine)
│   ├── compliance.py         # Code Compliance Agent (NBC 2016)
│   ├── scheduling.py         # Scheduling Agent
│   ├── workforce.py          # Workforce Matching Agent
│   ├── interior_design.py    # Interior Design Studio Agent
│   ├── metrics.py            # Prometheus custom metrics instrumentation
│   ├── auth.py               # Authentication & session security
│   ├── database.py           # SQLite database ORM & event wipe handler
│   └── tools/                # Centralized Tool Registry Package
│       ├── registry.py       # Tool dispatcher & audit trace logger
│       ├── material_prices.py# Regional unit cost database
│       ├── weather_api.py    # OpenWeatherMap site risk analysis
│       ├── nbc_lookup.py     # NBC 2016 compliance rules lookup
│       ├── calendar_engine.py# Dynamic timeline generation
│       └── json_report.py    # JSON report exporter
├── grafana/
│   ├── dashboards/
│   │   └── app_dashboard.json# Auto-provisioned Grafana dashboard JSON
│   └── provisioning/
│       ├── dashboards/       # Auto-loader configuration
│       └── datasources/      # Auto-wired Prometheus data source
├── monitoring/
│   ├── prometheus.yml        # Prometheus 5s scrape configuration
│   ├── buildsense-dashboard.json # Standalone dashboard template
│   ├── setup_windows_monitoring.ps1 # Native Windows installer
│   └── start_monitoring.ps1  # Native Windows process launcher
├── static/
│   ├── css/style.css         # Glassmorphic UI design system
│   └── js/main.js            # Vanilla JS frontend controller
├── templates/
│   ├── index.html            # Single Page Dashboard Interface
│   ├── login.html            # Authentication UI
│   └── register.html         # User Registration UI
├── tests/                    # 47 Unit & Integration test suites
│   ├── test_calendar_pipeline.py
│   ├── test_duration_planning.py
│   ├── test_duration_workforce.py
│   ├── test_pipeline_integration.py
│   └── test_tools.py
├── app.py                    # Flask REST API server with /metrics
├── Dockerfile                # Production Container Definition
├── docker-compose.yml        # Multi-container orchestration
├── gunicorn.conf.py          # Production WSGI server config
├── requirements.txt          # Python dependencies
└── README.md                 # Master Project Documentation
```

---

## 🚀 Quickstart & Installation Guide

### 1. Install Dependencies & Run Locally
```bash
pip install -r requirements.txt
python app.py
```

### 2. Configure Environment Variables (`.env`)
Copy `.env.example` to `.env` and configure your API keys:
```env
PORT=5000
GEMINI_API_KEY=your_gemini_key_here
GROQ_API_KEY=key1,key2,key3
GROQ_MODEL=openai/gpt-oss-20b
```

### 3. Run Automated Test Suite (100% Pass Rate)
```bash
python -m pytest tests/ -v
```

### 4. Access the Platform
Navigate to **http://localhost:5000** in your browser to log in and access the interactive dashboard.

---

## 🧪 Testing Without an API Key (Simulation Mode)

If API keys are not provided, BuildSense automatically runs in **Simulation Mode**:

1. Open the dashboard at `http://localhost:5000`.
2. Click **"Load Demo Renovations Blueprint"** — the schematic floor plan drawing loads with bounding box overlays.
3. Click the preset query chip: *"Can we finish Phase 2 within ₹15 lakh while compliant?"*
4. Watch the **Orchestration Map** light up agent-by-agent.
5. Review the **Coordinator's synthesized verdict** containing the itemized BOQ, NBC checks, labor schedule, and workforce allocations.

---

## 🛡️ Key Security & Engineering Design Decisions

* **Secure Authentication & Session Management:** Session management backed by persistent secrets and SQLite database isolation.
* **Fault-Tolerant Vision Pipeline:** Catch API errors (e.g. rate limits or network spikes) and fall back gracefully without locking the UI.
* **Dual-Mode Engine Architecture:** Seamless switching between Live API Mode and Offline Simulation Mode.
* **Localized Regulatory Compliance:** Tailored specifically to Indian Construction Standards (NBC 2016 Part 4 Fire & Life Safety).
* **Auditability & Reasoning Trail:** Surfacing individual specialist outputs alongside the synthesized verdict for full engineering auditability.

---

## ⚖️ Advantages & Current Limitations

### Advantages
* **High Mathematical Precision:** Python-driven calculation engines prevent LLM numerical errors.
* **Token & Cost Efficiency:** Shared inter-agent memory bus reduces redundant API invocations.
* **Modular Multi-Agent Extensibility:** Easily extendable with new domain agents (e.g., HVAC, Electrical, Plumbing).
* **Early Compliance Auditing:** Catch building code violations early in the schematic design phase.

### Limitations
* **Scan Quality Dependency:** Blueprint vision analysis requires legible, clear floor plan drawings.
* **API Rate Limits:** High-frequency API calls depend on cloud provider quotas (mitigated by key rotation and retries).

---

## 🔮 Future Roadmap

* **BIM (Building Information Modeling) Integration:** Direct ingestion and rendering of 3D CAD (`.dwg`, `.dxf`) and Revit (`.rvt`) files.
* **IoT Site Monitoring:** Integration with site sensors for real-time progress tracking and worker safety alerts.
* **Autonomous Bidding Engine:** Automated generation of formal tender submission packages for public construction projects.

---

## 📜 License & Credits

Developed for the **Infosys Springboard Virtual Internship** by **Rahul Mandal**.  
Project Architecture: **BuildSense — Multi-Agent AI Decision Support System**.
