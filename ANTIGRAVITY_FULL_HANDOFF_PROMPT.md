# Antigravity Full Handoff Prompt

Use the prompt below as the full end-to-end context for Antigravity.

```text
You are taking over an existing codebase called Horizon XL, formerly MiroFish.

Your job is to continue from the current repository state and finish the transformation from a graph-first report generator into a reliable, general-purpose, evidence-grounded, multi-agent future simulation system.

Do not restart from scratch. Inspect the current repository, preserve useful existing work, and fix the system end to end.

============================================================
1. Repository Context
============================================================

Local workspace:
/Users/ranik/Documents/trading app/MiroFish

Original upstream repository:
https://github.com/666ghj/MiroFish

Current local branch:
codex/horizon-xl-render-deploy

Current remotes:
origin = https://github.com/666ghj/MiroFish
renderrepo = https://github.com/Ranikricky/hackathon_usopen.git

Live URLs:
Frontend = https://horizon-xl.vercel.app
Backend = https://horizon-xl.onrender.com
Backend health = https://horizon-xl.onrender.com/health

The project began as MiroFish and has been rebranded toward Horizon XL. Some code, docs, comments, and assumptions may still reflect the original MiroFish design. User-facing behavior should now consistently say Horizon XL.

Current git working tree was not clean when this handoff was created. These files already had local modifications:
- backend/app/api/simulation.py
- frontend/src/api/index.js
- frontend/src/api/simulation.js
- frontend/src/components/Step3Simulation.vue
- frontend/src/views/SimulationRunView.vue

Do not blindly revert these files. Inspect them and preserve useful fixes.

============================================================
2. Latest Codex Inspection Findings
============================================================

This section is based on a read-only Codex inspection run on 2026-05-13. Treat it as the current known state before changing code.

Git:
- Current branch is codex/horizon-xl-render-deploy.
- Local HEAD is 752247f Parse colon-delimited agent prompts.
- renderrepo/codex/horizon-xl-render-deploy matches local HEAD.
- The branch is 35 commits ahead of origin/main.
- No stash was found.
- Dirty files before this regenerated handoff:
  - backend/app/api/simulation.py
  - frontend/src/api/index.js
  - frontend/src/api/simulation.js
  - frontend/src/components/Step3Simulation.vue
  - frontend/src/views/SimulationRunView.vue
  - ANTIGRAVITY_FULL_HANDOFF_PROMPT.md

Local environment:
- Root .env exists.
- Local LLM provider is OpenAI-compatible Moonshot/Kimi:
  - LLM_BASE_URL domain: api.moonshot.ai
  - LLM_MODEL_NAME: kimi-k2.5
  - LLM_API_KEY is set locally, but never print it.
- ZEP_API_KEY is set locally, but live Zep entity test returned 401 unauthorized.
- Supabase keys are not present locally.
- GITHUB_TOKEN is not present locally.
- Git store uses GIT_STORE_TOKEN, not GITHUB_TOKEN.
- No backend/.env, frontend/.env, frontend/.env.local, or frontend/.env.production was found.

Backend config:
- backend/app/config.py does not reference Supabase.
- backend/app/config.py uses generic LLM_BASE_URL and LLM_MODEL_NAME, not a hardcoded Kimi/Moonshot client.
- backend/app/__init__.py registers graph, simulation, and report blueprints.
- Local backend default port is 5001.
- backend/requirements.txt includes openai>=1.0.0.
- backend/requirements.txt includes zep-cloud==3.13.0.
- backend/requirements.txt does not include supabase.
- backend/requirements.txt does not include zep-python; it uses zep-cloud.

Local dev setup:
- System python3 is 3.9.6.
- backend/.venv was missing during inspection.
- The backend expects Python 3.11+ via Docker/uv.
- frontend/node_modules exists, so frontend deps are installed locally.

Live backend health:
- GET https://horizon-xl.onrender.com/health returned HTTP 200.
- Response included:
  - status: ok
  - service: Horizon XL Backend
  - build_marker: ontology_prompt_anchor_v3
  - configuration.ready: true
  - storage.active_provider: git
  - storage.git_configured: true
  - storage.zep_configured: true
- Important: live health says Git store and Zep are configured, but the direct Zep entity test returned 401 unauthorized. So "configured" does not mean "usable".

Live backend route audit:
- POST /api/simulation/plan exists. With project_id test it returned Project not found: test, meaning the route exists but requires a real project when project_id is supplied.
- GET /api/simulation/state/test_id exists and returned Structured simulation state not found.
- GET /api/simulation/validation/test_id exists and returned a validation diagnostic with passed=false.
- GET /api/simulation/sim_f8b15cb980f9/run-status exists and returned runner_status=idle, current_round=0, total_rounds=0, no actions.
- GET /api/simulation/sim_f8b15cb980f9/run-status/detail exists and returned idle with empty all_actions/twitter_actions/reddit_actions.
- GET /api/simulation/sim_f8b15cb980f9/timeline exists and returned an empty timeline.
- GET /api/simulation/list exists and returned one live simulation, sim_f8b15cb980f9.
- GET /api/report/list exists and returned success count=0.
- GET /api/graph/list does not exist and returned 404. The correct graph list-ish route is /api/graph/project/list.
- GET /api/graph/data/local_bd1c53b587d24afa exists and returned local graph data.
- GET /api/graph/local_bd1c53b587d24afa returned 404 because that route is wrong.
- GET /api/simulation/entities/test_graph_id returned a Zep 401 unauthorized error with traceback. This is a live bug: non-local Zep entity reads do not fail gracefully.

Active live simulation:
- Simulation ID: sim_f8b15cb980f9.
- Project ID: proj_a0c5033c5bd4.
- Graph ID: local_bd1c53b587d24afa.
- Live simulation status: ready/idle, not started.
- Twitter/reddit status: not_started.
- profiles_count: 15.
- entities_count: 15.
- The live transcript route exists:
  - GET /api/simulation/transcript/sim_f8b15cb980f9
- Transcript returned:
  - Turns: 0
  - Time pockets: 1
  - "No dialogue turns recorded for this pocket."
- Domain in transcript was bad/noisy:
  - "options investing chart investing subscribe livestream menu make"
- Project prompt visible in project list was:
  - "What will be the price of Bitcoin in 2026?"
- The ontology/plan appears polluted by web/page navigation text such as "Options Investing Chart Investing Subscribe Livestream Menu Make". This is a major root cause for wrong agents and irrelevant discussion.

Live graph for active simulation:
- local_bd1c53b587d24afa returned a local fallback graph.
- It had 15 nodes and 12 edges.
- Nodes were mostly generic orchestration/schema actors:
  - PrimaryDecisionMaker
  - AffectedParticipantGroup
  - ResourceController
  - DomainExpertAnalyst
  - GroundSignalReporter
  - DataProvider
  - NarrativeAmplifier
  - SimulationModerator
  - ExternalResearchScout
  - EvidenceAuditor
  - QuantitativeSynthesizer
  - NegotiationMediator
  - Person
  - Organization
  - DataRetrievalAnalyst
- This proves the generic control-agent layer exists, but the domain-specific actor extraction is still too weak/noisy for at least this run.

Frontend live:
- GET https://horizon-xl.vercel.app returned HTTP 200.
- HTML title: Horizon XL - Future Simulation Lab.
- Frontend asset bundle observed: /assets/index-CH6LeMkX.js.
- The shell had one temporary DNS failure for horizon-xl.vercel.app, then a later request succeeded. Treat network/DNS as intermittently unreliable during tests.

Frontend local code findings:
- frontend/src/api/index.js resolves API base URL dynamically.
- On vercel.app host, it returns https://horizon-xl.onrender.com.
- vercel.json has no /api rewrite to Render; frontend relies on JS baseURL fallback.
- frontend/src/components/Step3Simulation.vue still calls legacy POST /api/simulation/start.
- Step3Simulation does not call POST /api/simulation/run-structured.
- Step3Simulation polls:
  - GET /api/simulation/<id>/run-status
  - GET /api/simulation/<id>/run-status/detail
- Step3Simulation has a graph-memory-start fallback, but only for graph memory refusal.
- frontend/src/views/SimulationRunView.vue wraps loadGraph() in try/catch and degrades to an empty stale graph object.
- Graph refresh still runs every 30 seconds while simulating, which can keep surfacing graph/network errors.
- No transcript export button was found in Step3Simulation.
- BrandMark.vue itself uses overflow: visible and max-content sizing, so if the "L" clips, inspect parent containers/header CSS rather than only BrandMark.vue.

Report behavior:
- backend/app/api/report.py now blocks report generation with HTTP 409 if structured_state is missing or numeric validation fails.
- That 409 is expected under the new fail-closed design, but the UI must explain it clearly instead of looking broken.
- report_agent.py also has a validation gate and writes a "Simulation Evidence Insufficient" diagnostic instead of polished report when validation fails.

Most likely causes of the weeks-long failures:
1. Frontend still uses legacy /api/simulation/start for the discussion flow, while the newer structured runner lives at /api/simulation/run-structured. The structured state pipeline is not the main UI path.
2. Graph/Zep failures are only partially degraded. Local graph IDs work better, but Zep entity routes can still return traceback/401, and graph refresh can keep retrying/noising the UI.
3. Prompt/context cleaning is not strong enough. External research or scraped page text can pollute the domain plan, producing irrelevant agents and discussion.
4. Generic fallback graph/ontology exists, but it can be too generic and schema-like, causing agents such as PrimaryDecisionMaker instead of real prompt-specific actors.
5. Report generation correctly blocks on validation, but the frontend currently treats the 409 more like an error than a guided "run structured simulation first" state.

Exact files to inspect first:
- backend/app/api/simulation.py
- backend/app/services/zep_entity_reader.py
- backend/app/services/zep_graph_memory_updater.py
- backend/app/services/ontology_generator.py
- backend/app/services/domain_simulation_planner.py
- backend/app/services/agent_generation_engine.py
- backend/app/services/structured_simulation_runner.py
- backend/app/services/numeric_validation.py
- backend/app/api/report.py
- backend/app/services/report_agent.py
- frontend/src/api/index.js
- frontend/src/api/simulation.js
- frontend/src/views/SimulationRunView.vue
- frontend/src/components/Step3Simulation.vue
- frontend/src/components/BrandMark.vue

Likely deploy commands:
- Backend Render deploy path is probably:
  git push renderrepo codex/horizon-xl-render-deploy
- Vercel deploy path is unclear from local project metadata. Root .vercel project name is horizon-xl; frontend/.vercel project name is frontend. Confirm connected Git branch in Vercel dashboard before pushing.

============================================================
3. Product Vision
============================================================

Horizon XL should be a general-purpose simulation lab.

The core idea is:
One structured simulation state -> many possible outputs.

Supported domains must not be hardcoded to one use case. The system should work across:
- macroeconomic forecasting
- elections
- oil and commodity prices
- future of AI
- geopolitics
- market shocks
- consumer trends
- policy impact
- social/media narratives
- business strategy scenarios
- any other future-facing complex question

Outputs may include:
- research reports
- whitepapers
- executive memos
- news articles
- numeric forecast tables
- chart-ready JSON
- dashboards
- visualizations
- scenario timelines
- debate transcripts
- agent disagreement summaries

Important rule:
Outputs must be derived from the same structured simulation state. Output adapters must not invent new forecasts, numbers, agents, or causal claims.

============================================================
4. Current Architecture
============================================================

Backend:
Flask app in backend/

Important backend files:
- backend/run.py
- backend/app/__init__.py
- backend/app/config.py
- backend/app/api/graph.py
- backend/app/api/simulation.py
- backend/app/api/report.py
- backend/app/models/project.py
- backend/app/models/task.py
- backend/app/models/simulation_state.py
- backend/app/services/domain_simulation_planner.py
- backend/app/services/agent_generation_engine.py
- backend/app/services/structured_simulation_runner.py
- backend/app/services/simulation_runner.py
- backend/app/services/simulation_config_generator.py
- backend/app/services/simulation_manager.py
- backend/app/services/simulation_ipc.py
- backend/app/services/numeric_validation.py
- backend/app/services/ontology_generator.py
- backend/app/services/graph_builder.py
- backend/app/services/external_research.py
- backend/app/services/zep_tools.py
- backend/app/services/zep_graph_memory_updater.py
- backend/app/services/zep_entity_reader.py
- backend/app/services/durable_store.py
- backend/app/services/git_json_store.py
- backend/app/services/report_agent.py
- backend/app/services/output_adapters/__init__.py
- backend/app/services/output_adapters/report_adapter.py

Frontend:
Vue 3 app in frontend/

Important frontend files:
- frontend/src/main.js
- frontend/src/App.vue
- frontend/src/router/index.js
- frontend/src/api/index.js
- frontend/src/api/graph.js
- frontend/src/api/simulation.js
- frontend/src/api/report.js
- frontend/src/components/BrandMark.vue
- frontend/src/components/GraphPanel.vue
- frontend/src/components/Step1GraphBuild.vue
- frontend/src/components/Step2EnvSetup.vue
- frontend/src/components/Step3Simulation.vue
- frontend/src/components/Step4Report.vue
- frontend/src/components/Step5Interaction.vue
- frontend/src/views/Home.vue
- frontend/src/views/MainView.vue
- frontend/src/views/Process.vue
- frontend/src/views/SimulationView.vue
- frontend/src/views/SimulationRunView.vue
- frontend/src/views/ReportView.vue
- frontend/src/views/InteractionView.vue
- frontend/src/i18n/index.js
- locales/en.json
- locales/zh.json

Runtime/generated data:
- backend/uploads/projects
- backend/uploads/simulations
- backend/uploads/reports

Deployment files:
- Dockerfile
- docker-compose.yml
- docker-compose.prod.yml
- render.yaml
- vercel.json

============================================================
5. Existing Refactor Work Already Started
============================================================

Some new architecture modules already exist and should be inspected before adding new abstractions:

1. Domain planner:
backend/app/services/domain_simulation_planner.py

Expected purpose:
Given a user prompt and uploaded context, classify the domain, identify target variables, infer horizon/granularity, define scenario structure, and list required agent archetypes.

2. Agent generation:
backend/app/services/agent_generation_engine.py

Expected purpose:
Generate prompt-scoped, domain-adaptive, causally meaningful agents. Agents should include incentives, biases, trusted evidence, ignored evidence, forecasting method, blind spots, memory, numeric responsibilities, and revision rules.

3. Structured simulation state:
backend/app/models/simulation_state.py

Expected purpose:
Persist the simulation as structured JSON. This should be the source of truth for reports, charts, tables, and chat.

4. Structured simulation runner:
backend/app/services/structured_simulation_runner.py

Expected purpose:
Run time-pocket simulation and write structured state.

5. Numeric validation:
backend/app/services/numeric_validation.py

Expected purpose:
Validate that every required target variable, date, scenario, agent forecast, confidence, and unit exists before report generation.

6. Output adapter:
backend/app/services/output_adapters/report_adapter.py

Expected purpose:
Generate report content from simulation_state only. It should not invent numbers or claims.

7. Durable storage:
backend/app/services/durable_store.py
backend/app/services/git_json_store.py

Expected purpose:
Provide durable artifact storage without relying on Supabase. Supabase was tried and removed because it was not a viable option for the user.

============================================================
6. Key Recent Commit Themes
============================================================

The recent commit history includes work such as:
- Rebrand toward Horizon XL
- Fix brand mark clipping
- Context-derived agents
- Generic simulation planning
- Prompt-grounded ontology
- Prompt-scoped agents and edges
- More resilient stale graph loads
- Structured Horizon XL simulation pipeline
- Supabase durable graph store
- Removal/replacement with GitHub artifact storage
- Local graph index for durable lookup
- Actor filtering in ontology
- Explicit agent parsing
- Retry graph fetches through Render wakeups

Do not assume all of this is complete. Some pieces are partial and still need hardening.

============================================================
7. The Major Recurring Problem We Have Faced For Weeks
============================================================

The system repeatedly breaks or degrades at the graph/simulation/debate boundary.

Symptoms the user has seen:
- "Graph load failed - network error"
- "Graph load failed - request failed with status code 404"
- "Graph load failed - request failed with status code 503"
- Simulation discussion page gets slow or stalls
- Discussion does not start
- Report generation returns errors such as 409
- Debate appears unrelated to the prompt
- Election prompt produces generic/macro/trade/tariff style discussion
- Agents appear stale, generic, or copied from previous runs
- Report can sound polished even when graph/interview/numeric evidence is weak
- Final output sometimes has narrative but not enough numbers

The key expectation:
Graph loading failure must never hard-stop debate or report state progression. The graph is an evidence layer, not a single point of failure. If graph retrieval fails, the system should:
- use cached graph snapshot if available
- use structured simulation state if available
- continue in degraded mode with warnings
- preserve transcript and run status
- clearly show what evidence was missing
- prevent polished report generation only if required validation fails

============================================================
8. Conceptual Problem With Original MiroFish
============================================================

Original MiroFish was closer to:
Prompt/files -> ontology -> graph memory -> agents -> simulation/chat -> report agent.

That can produce impressive prose, but it can fail in important ways:
- graph is too thin but report still sounds confident
- agent interviews fail but report continues
- numeric forecasts are missing but report writes narrative
- agents are generic personas, not real causal actors
- debate can drift or repeat
- final report is not always traceable to structured simulation evidence

Horizon XL should instead be:
Prompt/context/web evidence -> domain simulation plan -> evidence graph -> adaptive agents -> time-pocket simulation -> structured simulation state -> numeric validation -> output adapters.

============================================================
9. Required Target Architecture
============================================================

The system must separate these layers:

1. Domain Simulation Planner
- Detect domain dynamically.
- Extract target variables.
- Define forecast horizon and granularity.
- Decide required state variables.
- Decide scenario structure.
- Decide what outputs are required.
- Enforce blind-simulation cutoff if present.

2. Evidence / Graph Layer
- Build graph from user prompt, uploaded text/files, and allowed external research.
- External research must be bounded and provenance-aware.
- The graph enriches simulation but is not the only truth source.
- Graph load failure must be recoverable.

3. Agent Generation Engine
- Generate context-specific agents.
- Do not use a fixed 10-agent framework.
- Do not hardcode Bengal election agents, macro agents, credit risk agents, or any other domain-specific cast.
- Agent count should depend on prompt scope, complexity, target variables, and social/system coverage.
- Include common people or lived-experience actors when relevant.
- Include experts, operators, strategists, institutional actors, and affected groups when relevant.
- Include moderator/mediator/research/quant roles where useful.

4. Time-Pocket Simulation Core
- Decide time unit dynamically.
- Macro may use monthly/quarterly.
- Election may use campaign phases, weeks, or event pockets.
- Oil may use daily/weekly/monthly.
- AI future may use quarterly/yearly.
- Geopolitics may be event-triggered.
- Each pocket must carry prior state forward.

5. Structured Simulation State Store
- Write JSON state to disk/durable store.
- This state is source of truth.

6. Numeric Validation Layer
- Validate before polished output.
- If incomplete, block report and show diagnostic.

7. Output Adapter Layer
- Report, whitepaper, news article, executive memo, numeric table, chart JSON, visualization.
- Adapters must only consume simulation_state.

============================================================
10. Required Simulation State Shape
============================================================

simulation_state.json should include at least:

{
  "simulation_id": "...",
  "project_id": "...",
  "domain_plan": {},
  "agents": [],
  "time_pockets": [],
  "state_variables": [],
  "agent_outputs": [],
  "scenario_outputs": {},
  "aggregated_outputs": {},
  "validation": {},
  "cutoff_date": null,
  "future_leakage_policy": {},
  "created_at": "...",
  "updated_at": "..."
}

Each time pocket should include:

{
  "pocket_id": "...",
  "label": "...",
  "start": "...",
  "end": "...",
  "events": [],
  "state_before": {},
  "agent_actions": [],
  "agent_forecasts": [],
  "cross_agent_interactions": [],
  "moderator_summary": {},
  "research_updates": [],
  "quant_checks": [],
  "state_after": {},
  "triggered_revisions": []
}

Each agent forecast must include:

{
  "agent_id": "...",
  "pocket_id": "...",
  "target_variable": "...",
  "forecast_path": [
    {
      "date": "...",
      "value": 0.0,
      "unit": "...",
      "scenario": "base | upside | downside | tail"
    }
  ],
  "confidence": 0.0,
  "reasoning_summary": "...",
  "drivers": [],
  "risks": [],
  "what_would_change_my_forecast": [],
  "blind_spots": []
}

============================================================
11. Agent Design Requirements
============================================================

Agents must not be shallow personas.

Each generated agent should include:
- agent_id
- name
- domain
- role
- causal_power
- institutional_incentives
- skin_in_the_game
- information_set
- trusted_data_sources
- ignored_or_underweighted_data
- forecasting_method
- heuristics
- biases
- blind_spots
- prior_beliefs
- memory
- numeric_capabilities
- emotional_style
- communication_style
- analytical_strength
- uncertainty_tolerance
- revision_rules
- relationships to other agents

Important:
Not every agent should behave like an economist. Common people, factory workers, voters, customers, households, and beneficiaries may not speak in formal numbers. They can provide lived signals, pressure, sentiment, constraints, and behavioral reactions. The mediator/quant/research agents can translate those signals into structured model inputs.

The simulation should allow:
- strategists arguing strategically
- experts using theory/data
- common people describing lived effects
- business/institutional actors optimizing incentives
- moderator summarizing and challenging drift
- quant/research agent checking numbers and evidence
- mediator deciding which claims should affect next-round state

============================================================
12. Debate Requirements
============================================================

The debate must be real enough to produce value, not a staged script that just restates the prompt.

Every pocket should include:
- agent positions
- explicit disagreements
- evidence cited
- moderator intervention
- quant/research check when numeric claims appear
- revised state
- unresolved uncertainties
- carry-forward assumptions

The moderator should not merely copy the last expert. It should evaluate:
- which claims are evidence-backed
- which claims are biased
- which claims are weak or unsupported
- which numbers need validation
- which disagreement should be explored next

The transcript should be exportable in a readable full text format.

============================================================
13. Dynamic Agent Count
============================================================

Do not force exactly 10 agents.

Agent count should be a function of:
- prompt complexity
- number of target variables
- number of affected populations
- number of decision-making institutions
- number of geographic or sectoral regions
- evidence density
- time horizon length
- scenario complexity

Example:
If the outcome is heavily affected by ordinary people, voters, households, consumers, or workers, those groups should occupy a meaningful share of the agent set. They can be segmented by relevant dimensions discovered from the prompt and research context.

Do not hardcode:
- Bengal election agents
- macro/credit agents
- oil agents
- AI future agents
- any other domain-specific list

Domain-specific nouns may appear only because the prompt/evidence/research makes them important.

============================================================
14. Graph Requirements
============================================================

The graph should include:
- entities from prompt
- entities from uploaded files/text
- entities from allowed web/external research
- relationships between actors, institutions, events, variables, and claims
- evidence provenance
- confidence or source quality when possible

Graph limitations must be visible:
- if graph has few nodes/edges, show that
- if graph is stale, show that
- if graph could not load, show warning
- if graph came only from prompt and not research, show that

Graph search means querying the small graph memory, not asking ChatGPT directly. Make this distinction clear in code comments/user-facing diagnostics where needed.

============================================================
15. Web Research Requirements
============================================================

External research should be optional, bounded, and policy-aware.

It should feed:
- ontology generation
- graph construction
- agent background context
- research/quant agents during debate

It must respect cutoff date:
If user says "using only information available up to DATE", research after DATE must be blocked or flagged.

Research should not silently become a source of leakage.

============================================================
16. Numeric Discipline
============================================================

For forecast/prediction questions, numeric outputs are mandatory.

Every forecast must have:
- value
- date/time pocket
- unit
- target variable
- scenario
- agent_id
- confidence

Do not produce reports with vague prose if numeric outputs are missing.

If user asks for monthly forecasts, do not interpolate after the fact. The system should simulate sequentially:
state at time t -> agent updates -> forecast for t+1 -> update state -> next pocket.

More generally:
Any simulation should decide a basic future time unit or event unit and run in pockets one after another.

============================================================
17. Validation Requirements
============================================================

Validation output should include:

{
  "passed": true,
  "errors": [],
  "warnings": [],
  "missing_agents": [],
  "missing_variables": [],
  "missing_dates": [],
  "missing_scenarios": [],
  "numeric_quality_score": 0.0
}

If validation fails, report generation should return:
"Simulation evidence insufficient"

It must show:
- what failed
- which agents failed
- what variables are missing
- what dates are missing
- what scenarios are missing
- how to rerun/fix

============================================================
18. Report Requirements
============================================================

Report generation must:
1. Load simulation_state.
2. Run validation.
3. Inspect numeric outputs.
4. Inspect agent outputs.
5. Inspect scenario paths.
6. Generate polished report only if validation passes.

Required sections:
- Executive summary
- Simulation setup
- Domain and target variables
- Agent architecture
- Numeric forecast outputs
- Scenario comparison
- Agent disagreement
- Key causal mechanisms
- Trigger events and revisions
- Confidence and uncertainty
- Blind spots
- Appendix tables

Reports must not invent numbers.

============================================================
19. Frontend Requirements
============================================================

The frontend should guide the user through:

1. Prompt/context input
2. Domain Simulation Plan
3. Agent Generation
4. Simulation Run
5. Validation
6. Output Studio
7. Chat with simulation

Current frontend is Vue 3.

Important UI issues:
- Graph load failure should be visible but non-blocking.
- Discussion should not freeze because graph view cannot render.
- User should be able to see/download full debate transcript.
- Branding should be Horizon XL.
- The "L" in the Horizon XL mark was reported as cropped and should be checked.
- UI should feel simpler, futuristic, classy, and distinct from old MiroFish.

============================================================
20. API Requirements
============================================================

Existing/suggested endpoints to preserve or complete:

POST /api/simulation/plan
Input: project_id, prompt
Output: domain simulation plan

POST /api/simulation/generate-agents
Input: project_id, domain_plan
Output: agents

POST /api/simulation/run-structured
Input: project_id, domain_plan, agents
Output: simulation_id, task_id or state

GET /api/simulation/state/<simulation_id>
Returns simulation state JSON

GET /api/simulation/validation/<simulation_id>
Returns validation result

GET /api/simulation/<simulation_id>/run-status/detail
Returns live status and recent timeline actions without overloading frontend

POST /api/report/generate
Must be validation-gated

GET /api/report/<report_id>
Fetch report

Also add/ensure output routes if not already present:
POST /api/outputs/generate
GET /api/outputs/<simulation_id>/<output_type>

============================================================
21. Deployment Context
============================================================

The user has used:
- Render backend
- Vercel frontend
- Cloudflare was considered temporary
- Supabase is not an option anymore
- GitHub-backed storage was introduced as fallback
- Zep has been unreliable/unavailable for user

The system should work without Supabase.

If Zep is missing/unavailable:
- graph memory should degrade to local/GitHub store
- user should get clear warning
- simulation should not collapse

============================================================
22. Acceptance Tests
============================================================

Run these before claiming success.

Test 1: Macro unemployment simulation
Prompt:
"Using only information available up to Dec 2007, simulate how different economic agents would forecast U.S. unemployment for 2008-2010. Do not use actual future unemployment data."

Expected:
- domain = macro
- target variable includes unemployment rate
- monthly or quarterly forecast path
- agents include central bank, banks, businesses, labor/households, media, academics or equivalent dynamically inferred actors
- each required numeric actor or quant translation produces numbers
- no post-Dec-2007 leakage
- report includes numeric forecast tables

Test 2: West Bengal election simulation
Prompt:
Use a compact West Bengal 2026 Assembly election prompt with historical election baselines, regional dynamics, turnout, women/minority turnout, TMC/BJP/Left-Congress/Others vote and seat share, uncertainty bands, and cutoff date May 1 2026.

Expected:
- domain = election
- target variables include vote share, seat share, turnout, scenario probabilities
- agents are relevant but dynamically generated
- no stale macro/credit/trade/tariff discussion
- common people/voter segments are represented if prompt suggests they matter
- no hardcoded Bengal list unless prompt explicitly supplied it
- numeric tables appear

Test 3: Oil price simulation
Prompt:
"Forecast Brent oil prices over the next 12 months considering OPEC, China demand, U.S. shale, inventories, and geopolitical risk."

Expected:
- domain = oil/commodity
- agents include relevant oil actors dynamically
- output includes base/upside/downside/tail price paths

Test 4: Future of AI
Prompt:
"Simulate the future of AI adoption over the next 5 years across enterprises, regulators, frontier labs, open-source developers, and workers."

Expected:
- domain = ai_future
- target variables include adoption, regulation, capex, labor disruption or similar
- agents reflect labs, enterprises, regulators, workers, investors, open source, etc.
- numeric and qualitative outputs exist

Test 5: Validation failure
Manually remove an agent forecast or required target variable from simulation state.

Expected:
- report generation blocked
- diagnostic generated
- no polished report

Test 6: Graph failure resilience
Force graph endpoint to return 404/503 or use stale graph id during debate page.

Expected:
- frontend shows warning
- discussion/timeline does not hard-stop
- status polling continues
- transcript remains available
- report validation decides whether output can proceed

============================================================
23. What To Fix First
============================================================

Start with backend flow, then frontend.

Priority 1:
backend/app/api/simulation.py
- inspect start route
- inspect run-status/detail
- inspect graph_id requirements
- inspect graph memory update fallbacks
- ensure missing graph does not hard-stop run

Priority 2:
backend/app/services/ontology_generator.py
- remove domain-specific hardcoded actor casts
- ensure prompt-scoped extraction
- make relationship edges accurate and evidence-based

Priority 3:
backend/app/services/agent_generation_engine.py
- dynamic count
- prompt-specific but not hardcoded
- common-people weighting
- mediator/moderator/quant/research roles
- no stale memory bleed

Priority 4:
backend/app/services/structured_simulation_runner.py
- real pocket-based state update
- transcript capture
- moderator summary
- numeric forecast generation

Priority 5:
backend/app/services/numeric_validation.py
- strict completeness checks
- fail closed

Priority 6:
backend/app/services/report_agent.py
backend/app/services/output_adapters/report_adapter.py
- ensure output reads simulation state only

Priority 7:
frontend/src/components/Step3Simulation.vue
frontend/src/views/SimulationRunView.vue
frontend/src/api/simulation.js
frontend/src/api/index.js
- robust status polling
- graph warning but no freeze
- transcript export
- clear validation state

============================================================
24. Current Local Commands
============================================================

Useful commands:

cd '/Users/ranik/Documents/trading app/MiroFish'
git status --short
git log --oneline --decorate -n 30
git diff --stat
npm run build
npm run dev
npm run backend
npm run frontend
curl http://localhost:5001/health

Health endpoint currently exists at:
/health

============================================================
25. Required Final Handoff From You
============================================================

When done, return:

1. Files changed
2. Root cause analysis for each recurring issue
3. What you implemented
4. What commands/tests you ran
5. Exact results of each acceptance test
6. Remaining limitations
7. Deployment steps if any
8. Whether Vercel frontend + Render backend are ready

Do not claim success unless:
- graph failure no longer blocks discussion
- dynamic agent generation is not hardcoded to Bengal/macro/credit
- report generation is validation-gated
- transcript can be inspected/exported
- at least one full frontend-to-backend sample run works or the exact blocker is documented

============================================================
26. Tone Of Implementation
============================================================

Be conservative with existing code.
Do not rewrite the whole app.
Prefer focused patches that respect current project structure.
Do not reintroduce Supabase.
Do not leak API keys.
Do not remove user work.
Do not hide failure behind polished prose.
```
