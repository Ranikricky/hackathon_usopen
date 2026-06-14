# Horizon XL Full Codebase Review Prompt for ChatGPT

You are ChatGPT acting as a senior full-stack AI systems architect, product designer, forecasting-methodology reviewer, and codebase auditor.

I am giving you a zipped codebase snapshot of **Horizon XL**, formerly **MiroFish**. Please review it deeply and tell me what is real, what is incomplete, what is broken, what is misleading, and what should be fixed next.

Do not assume the product works just because files or UI labels exist. Inspect the actual code paths, data flow, validation, UI behavior, and generated outputs.

## Live URLs

- Frontend: https://horizon-xl.vercel.app
- Backend: https://horizon-xl.onrender.com
- Local repo path when created: `/Users/ranik/Documents/trading app/MiroFish`
- Current branch when packaged: `codex/horizon-xl-render-deploy`
- Recent commits:
  - `1e4ea1e Merge Render main deployment history`
  - `55c8a97 Refactor Horizon XL structured simulation flow`
  - `752247f Parse colon-delimited agent prompts`
  - `142eb61 Retry graph fetches through Render wakeups`
  - `565b8bf Preserve explicit agents in ontology fallback`

## Why This Project Exists

Horizon XL is intended to become a **general-purpose future simulation and forecasting lab**, not a social-media toy and not a plain report generator.

The desired product loop is:

```text
User prompt / files / URLs
→ Domain Contract
→ Evidence / Signal Map
→ Agent Society
→ Debate Readiness
→ Structured Time-Pocket Debate
→ Belief Revision
→ Forecast Ledger
→ Validation
→ Output Studio
→ Ask State
```

The core product should not be “agents talking.” The core product should be:

```text
Forecast Thesis
→ Evidence
→ Disagreement
→ Structured Debate
→ Belief Revision
→ Forecast Ledger
→ Validated, readable report
```

The final report must be generated from validated structured forecast state, not from raw transcript logs or freeform LLM prose.

## Original Problem

MiroFish originally behaved more like a graph-first OASIS/social simulation/report generator:

- It could generate polished reports even when evidence was weak.
- Graph load failures could break the UI or flow.
- Agent conversations sounded robotic and generic.
- Agents often repeated roles instead of debating.
- Reports were summary-like, not useful or enjoyable to read.
- Domain-specific prompts sometimes produced stale/wrong agents from prior contexts.
- Numeric outputs could be malformed or meaningless.
- Validation could pass structurally but fail semantically.
- Legacy Twitter/Reddit/social-discourse assumptions leaked into non-social prompts.
- The UI claimed features that were only partial.

The user wants Horizon XL to be far away from MiroFish now.

## Product Goal

Horizon XL should support many future-facing domains:

- geopolitics
- elections
- commodities
- AI adoption
- policy impact
- business strategy
- narrative fiction
- sports and entertainment futures
- public-health and climate risk
- social/media narratives only when explicitly requested

It must be generic. Do not hardcode Bengal elections, Iran, oil, AI, ASOIAF, or any single prompt. Domain-specific terms such as seats, vote share, price, inventory, adoption rate, character survival, alliance probability, or market share are allowed only as generic domain-target concepts selected from prompt/context/domain-contract logic.

## Desired Architecture

The target architecture is:

1. **Domain Contract**
   - Separates evidence from instructions.
   - Extracts domain, engine mode, targets, actors, horizon, output requirements, guardrails, rejected prompt fragments.

2. **Evidence / Signal Map**
   - Builds a working evidence graph from prompt/files/URLs and ideally external research.
   - The graph should be working memory, not “the bible.”
   - Research should be able to update/augment the graph during a session.

3. **Agent Society**
   - Generates meaningful actor groups, not shallow personas.
   - Actors should be people, groups, institutions, organizations, or fictional actors when relevant.
   - Agents need incentives, blind spots, allowed claims, forbidden claims, evidence scope, must-not-know constraints, IQ/EQ/game-theory style differences, and skin-in-the-game.
   - Common people / affected participant classes should be weighted when they drive the outcome.
   - Moderator/research/quant/mediator/auditor roles should be real orchestration roles, not just labels.

4. **Structured Simulation Runner**
   - Default engine for normal forecasting.
   - Legacy OASIS/Twitter/Reddit simulation should only run when `engine_mode = "social_discourse"`.
   - Debate should be dispute-driven:
     - thesis presentation
     - evidence audit
     - cross-questioning
     - mechanism clash
     - rebuttal
     - concession
     - forecast revision
     - scenario synthesis
     - quant/qual synthesis
     - debate quality validation

5. **Forecast Ledger**
   - Main structured output.
   - Stores targets, before/after forecasts, support, dissent, assumptions, disputes, confidence, uncertainty, and what would change the forecast.

6. **Validation**
   - Blocks structurally present but semantically useless output.
   - Should reject instruction fragments as targets, generic placeholders, wrong-domain targets, empty final outcomes, weak debate quality, missing rebuttals/concessions/revisions, and reports that have no useful forecast.

7. **Output Studio**
   - Generates reports, charts, numeric tables, memos, news/article styles from the same structured state.
   - Output adapters must not invent new facts, forecasts, or agents.

8. **Ask State**
   - Should answer questions from saved structured state, agent debate, numeric outputs, evidence trail, and validation.
   - It must be grounded; it should not be a freeform hallucinating chat.

## Current Feature Claims That Need Verification

The homepage currently describes a simulation stack:

1. **Signal Map**
   - “Prompt, files, URLs, and external research become a working evidence map.”
   - Please verify whether URLs/external research truly feed the graph, or whether this is only partial/provider-dependent.

2. **Agent Lab**
   - “Domain-relevant actors are generated with incentives, blind spots, and information access.”
   - Verify if agents are genuinely domain-relevant and not generic jargon.

3. **Pocket Run**
   - “Agents act through sequential time pockets while moderators, research, and quant roles challenge weak claims.”
   - Verify whether time pockets are really sequential and whether moderator/research/quant intervene meaningfully.

4. **Output Studio**
   - “Reports, tables, charts, memos, and summaries are generated from the same structured state.”
   - Verify if reports consume ledger/state only and if charts are real or chart-ready JSON.

5. **Ask State**
   - “Chat with saved simulation state, agent debate, numeric outputs, and evidence trail.”
   - Recently this was changed toward a deterministic `/api/outputs/ask` endpoint. Verify if it is complete and useful.

## Very Recent Local Changes Before Packaging

The codebase contains local modifications not necessarily committed.

Important recently changed or added files include:

- `backend/app/api/outputs.py`
  - Added `POST /api/outputs/ask`.
  - It reads saved structured simulation state and answers deterministically from `domain_plan`, `forecast_thesis`, `forecast_ledger`, `dispute_registry`, `assumption_registry`, `discussion_transcript`, `aggregated_outputs`, and `validation`.
  - It intentionally does not call the LLM.

- `frontend/src/api/outputs.js`
  - Added `askStructuredState()`.

- `frontend/src/components/HorizonStructuredWorkbench.vue`
  - Added Ask State card.
  - Added more structured “Forecast desk” workflow language.
  - UI has live room, audit transcript, final state, output adapters, published report, and Ask State.

- `frontend/src/views/Home.vue`
  - Fixed large hero `Horizon XL` brand clipping by changing the later overriding CSS block:
    - `.hero-logo { overflow: visible; padding: 44px 72px 44px 44px; }`
    - constrained `.hero-logo-main` and `.hero-logo-main .brand-mark`.
    - added responsive guard.
  - Local visual check showed:
    - card width: 594
    - brand mark width: 456
    - `overflowX: 0`
    - visible text: `Simulation LabHorizonXL`

- `locales/en.json`, `locales/zh.json`
  - Feature copy was partially made more honest:
    - “million-scale agents” wording toned down.
    - Ask State described more carefully.
    - External research described as provider-dependent.

- `backend/app/services/forecast_artifacts.py`
  - New artifact-related service file.

Other modified files at packaging time:

- `backend/app/api/graph.py`
- `backend/app/api/simulation.py`
- `backend/app/config.py`
- `backend/app/models/simulation_state.py`
- `backend/app/services/agent_generation_engine.py`
- `backend/app/services/domain_contract.py`
- `backend/app/services/domain_simulation_planner.py`
- `backend/app/services/forecast_ledger.py`
- `backend/app/services/numeric_validation.py`
- `backend/app/services/ontology_generator.py`
- `backend/app/services/output_adapters/report_adapter.py`
- `backend/app/services/output_adapters/report_template_registry.py`
- `backend/app/services/structured_simulation_runner.py`
- `backend/app/utils/llm_client.py`

## Known Current Concerns

Please pay close attention to these. They have been recurring problems for weeks:

1. **LLM connectivity**
   - The app previously logged: `Planner LLM failed, using context-derived fallback: Connection error`.
   - The user upgraded the API plan/subscription and wants LLM API calls to work 100% of the time.
   - Recent local changes aimed to fail closed when LLM planning is required instead of silently using fallback.
   - Review `backend/app/config.py`, `backend/app/utils/llm_client.py`, and `backend/app/services/domain_simulation_planner.py`.
   - Check whether the fallback is still hiding failures.

2. **Graph errors**
   - User repeatedly saw “graph load failed - network error.”
   - The desired behavior is not merely “graph failure does not block UI”; the desired behavior is that graph loading should work reliably.
   - Still, if a graph provider is down, the simulation should degrade honestly and not crash.
   - Review `backend/app/api/graph.py`, `zep_tools.py`, `zep_entity_reader.py`, and frontend graph polling.

3. **Semantic nonsense passing validation**
   - Earlier reports had targets like `thefollowingnumericoutputs`.
   - Earlier reports said “Final outcome exists, but no winner/seat/vote fields were available.”
   - Validation must block this.
   - Review `numeric_validation.py`, `forecast_ledger.py`, `structured_simulation_runner.py`, and report adapters.

4. **Bad transcripts**
   - Agent transcript often sounded like machines stating role cards:
     - “I’m coming in institutional, cautious…”
     - “My stake is career, reputation…”
   - User wants lively human debate:
     - arguments
     - rebuttals
     - cross-questions
     - evidence
     - anecdotes
     - concessions
     - moderator steering
     - research/quant interruptions
   - Review `structured_simulation_runner.py` and any debate prompt/turn generation logic.

5. **Reports are not useful/readable**
   - Reports often feel like backend summaries, not compelling reports/blogs/articles.
   - User wants:
     - readable narrative
     - charts/visuals
     - quote cards
     - tweet-like pullouts (clearly synthetic, not fake real tweets)
     - more story/explanation
     - less raw transcript/agent-number inventory
   - Review `report_adapter.py`, `report_template_registry.py`, and frontend report rendering.

6. **Domain contamination / memory leakage**
   - Different prompts sometimes feel like they copy previous prompt results.
   - The product must not memorize Bengal/election/credit-risk assumptions by default.
   - Review storage, graph index, project state, agent generation, ontology generation, and any global cached variables.

7. **Legacy OASIS/social assumptions**
   - Legacy `simulation_runner.py` should not be default for normal forecast prompts.
   - Social simulation should only be used for social-discourse prompts.
   - Review API route selection and engine mode boundaries.

8. **Frontend UX**
   - User disliked several right-side layouts.
   - They want a simpler, classy, newspaper/report-like theme.
   - UI should explain the pipeline clearly and not expose raw backend artifacts as the primary experience.
   - Review `Home.vue`, `SimulationRunView.vue`, `Step3Simulation.vue`, `HorizonStructuredWorkbench.vue`.

## What To Review In The Codebase

Please inspect these first:

### Backend

- `backend/app/api/simulation.py`
- `backend/app/api/graph.py`
- `backend/app/api/report.py`
- `backend/app/api/outputs.py`
- `backend/app/config.py`
- `backend/app/models/project.py`
- `backend/app/models/simulation_state.py`
- `backend/app/services/domain_contract.py`
- `backend/app/services/domain_simulation_planner.py`
- `backend/app/services/ontology_generator.py`
- `backend/app/services/agent_generation_engine.py`
- `backend/app/services/structured_simulation_runner.py`
- `backend/app/services/simulation_runner.py`
- `backend/app/services/simulation_config_generator.py`
- `backend/app/services/forecast_artifacts.py`
- `backend/app/services/forecast_ledger.py`
- `backend/app/services/numeric_validation.py`
- `backend/app/services/output_adapters/report_adapter.py`
- `backend/app/services/output_adapters/report_template_registry.py`
- `backend/app/services/report_agent.py`
- `backend/app/services/zep_tools.py`
- `backend/app/services/zep_entity_reader.py`
- `backend/app/services/zep_graph_memory_updater.py`
- `backend/app/utils/llm_client.py`

### Frontend

- `frontend/src/views/Home.vue`
- `frontend/src/views/SimulationRunView.vue`
- `frontend/src/components/Step3Simulation.vue`
- `frontend/src/components/HorizonStructuredWorkbench.vue`
- `frontend/src/api/index.js`
- `frontend/src/api/simulation.js`
- `frontend/src/api/graph.js`
- `frontend/src/api/report.js`
- `frontend/src/api/outputs.js`
- `frontend/src/router/index.js`

### Deployment

- `render.yaml`
- `vercel.json`
- `frontend/package.json`
- `backend/requirements.txt`

## Test Prompts To Use

Please test or mentally simulate these:

1. **Geopolitics**
   - “What is the likely outcome of a US-Iran conflict over the next 90 days?”
   - Expected: geopolitical structured forecast, no social-media engine, thesis/disputes/forecast ledger present.

2. **Election**
   - “Who is likely to win the 2026 West Bengal election?”
   - Expected: election targets such as seat share, vote share, turnout, swing regions. No generic social agents unless public discourse requested.

3. **Commodity**
   - “Forecast lithium prices over the next 12 months.”
   - Expected: price range, supply, demand, inventory, China/EV demand, substitution, no election/social targets.

4. **Narrative Fiction**
   - “What will happen in The Winds of Winter?”
   - Expected: narrative fiction forecast, character/faction/plot target classes, no fake numeric validation required.

5. **Social Discourse**
   - “How will Twitter react if a celebrity scandal breaks tomorrow?”
   - Expected: social_discourse engine may be used; legacy social simulation allowed.

## Acceptance Criteria

A run should be considered successful only if:

- Domain Contract is created before graph/simulation.
- Engine mode is correct.
- Targets are meaningful for the domain.
- Forecast thesis exists.
- Assumptions exist.
- Disputes exist.
- Agents map to disputes and targets.
- Debate includes challenge, rebuttal, concession, and revision.
- Forecast ledger shows before/after movement or justified no-change.
- Report consumes the ledger, not raw logs.
- Validation blocks nonsense before final report.
- UI copy does not overclaim features that do not exist.
- Graph load is reliable or degrades clearly without killing the flow.
- LLM unavailable state is explicit and not hidden by fake fallback outputs.

## Specific Questions To Answer

Please answer these explicitly:

1. Is the structured forecasting path actually the default for non-social prompts?
2. Is legacy social/OASIS logic still leaking into normal prompts?
3. Does the Domain Contract truly separate evidence, instructions, targets, actors, and rejected fragments?
4. Are targets extracted generically, or are there hidden hardcoded domain patches?
5. Does the graph include research/context, or mostly just prompt-derived nodes?
6. Does graph failure still cause network errors in the frontend?
7. Are agents meaningful actors, or mostly generated labels?
8. Do moderator/research/quant roles actually change the debate?
9. Does the transcript read like a real debate or like mechanical role statements?
10. Does validation block semantically useless reports?
11. Does the final report answer the actual prompt in a human-readable and useful way?
12. Is Ask State real and grounded?
13. Are homepage feature claims accurate?
14. Are there deployment/environment issues likely to break Vercel/Render?
15. What are the top 10 highest-leverage fixes?

## Requested Output From ChatGPT

Produce:

1. **Architecture Autopsy**
   - Explain what exists, what is partial, and what is fake/overclaimed.

2. **Data Flow Map**
   - Show how prompt → domain contract → graph → agents → simulation → ledger → report actually flows.

3. **Failure Map**
   - List where things can fail and what symptoms the user sees.

4. **Product Gap Review**
   - Compare current product against desired Horizon XL vision.

5. **Code-Level Findings**
   - File-by-file findings with severity.

6. **Refactor Plan**
   - Shortest safe path to make the product meaningfully better.

7. **Frontend/UX Recommendations**
   - Make it more usable, beautiful, honest, and report-like.

8. **Prompt/Simulation Methodology Recommendations**
   - How to make debates and reports genuinely insightful.

9. **Deployment/Env Checklist**
   - What keys/configs must be set and how to test them.

10. **Prioritized Action Plan**
    - What to fix first, second, third.

Be critical. Do not flatter the project. The goal is to make Horizon XL genuinely useful, not merely visually nicer.

