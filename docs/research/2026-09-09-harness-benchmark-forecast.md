# LANTU Harness Benchmark Forecast

Date: 2026-09-09

Scope: Forecast the likely effect of LANTU's current harness on coding-agent benchmarks. The retrieval work in a separate window is excluded. This is a reasoned forecast, not a benchmark result.

## Executive forecast

LANTU is likely to be above a minimal tool-calling scaffold on long, multi-step coding tasks, especially tasks that require repeated inspection, artifact recovery, context pressure handling, or continuation after a restart. The strongest expected gains are reliability and trajectory completion, not a guaranteed large pass-rate increase on short, self-contained bug fixes.

No defensible percentage score can be predicted yet. SWE-bench and Terminal-Bench scores are dominated by the model, repository/task distribution, tool/retrieval quality, and test execution. The current harness has been validated on targeted scenarios, but not on a statistically representative benchmark sample.

## What the benchmarks actually measure

### SWE-bench / SWE-bench Verified

The official harness evaluates a submitted patch inside a reproducible Docker environment and re-runs the task tests. The quickstart also recommends evaluating a gold patch to validate the evaluation setup. See the [official SWE-bench quickstart](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/quickstart.md).

SWE-bench Verified is a human-validated subset intended to remove infeasible samples. OpenAI reported GPT-4o's best open scaffold at 33.2% on Verified versus 16% on the original set, showing that dataset construction and scaffold choice materially affect the measured result ([OpenAI's Verified report](https://openai.com/index/introducing-swe-bench-verified/)). The report also warns that static public GitHub tasks have contamination and coverage limitations.

Implication for LANTU: context persistence, artifact references, and recovery can prevent trajectory failures, but they only improve the score when the model can identify the right code change and produce a test-passing patch. Retrieval quality is therefore a larger direct bottleneck than cache efficiency for most SWE-bench instances.

### Terminal-Bench

The official project defines Terminal-Bench as an end-to-end test in a real terminal environment. The benchmark is continuously versioned, and Harbor runs each agent in a sandbox against task-specific verification ([Terminal-Bench README](https://github.com/harbor-framework/terminal-bench), [Harbor run guide](https://www.harborframework.com/docs/tutorials/running-terminal-bench)). The task format exposes verifier-read artifacts and separate agent/verifier timeouts, so final filesystem state and verification—not the transcript alone—determine success ([task template](https://github.com/harbor-framework/terminal-bench/blob/main/docs/task-template.toml)).

Implication for LANTU: the Journal, artifact persistence, restart recovery, and window rollover are directly relevant to long-running terminal tasks. Correct command execution, shell ergonomics, timeout handling, and repository inspection still dominate the result; these are not established by the current targeted tests alone.

### AgentBench (setup-focused comparison)

AgentBench explicitly says it benchmarks the agent setup rather than only the model: 40 rule-scored tasks across seven domains, with memory, cross-session handoff, multi-step work, and noise-vs-signal tasks among the dimensions. Its README states that two users with the same model can differ substantially based on agent configuration ([AgentBench repository](https://github.com/agentbench/agentbench)).

Implication for LANTU: this is the closest public framing for isolating harness value. LANTU's session journal, structured compaction, and window grouping should be testable as configuration deltas, but the claim must be measured with the same model, prompts, task seeds, and tool budget.

## Harness factors likely to help LANTU

| Harness capability | Likely benchmark effect | Confidence |
| --- | --- | --- |
| Journal-backed session recovery | Fewer failures after long trajectories or process restarts | Medium-high |
| Artifact files with durable references | Prevents loss of large tool outputs; allows re-reading evidence | Medium |
| Local stale-tool eviction and structured compaction | More usable turns before context overflow | Medium-high |
| Same-session window rollover | Allows continuation on unusually long tasks without replaying all history | Medium |
| FileLedger and Schema Epoch preservation | Reduces state drift after compaction/window changes | Medium |
| Prefix-stable system/tool schema and appendix updates | Lower latency/cost and less cache churn; indirect score effect | High for cost/latency, low-medium for pass rate |
| Lens/window observability | Faster debugging and iteration; no direct task score effect | High |
| Grep single-file support | Removes a concrete tool usability failure | Medium, task-dependent |

These are directional inferences from LANTU's design and targeted validations. They are not results from an official benchmark run.

## Main limits and risks

1. **Retrieval remains the largest known gap.** If the agent cannot find the relevant files or symbols, preserving context does not create the missing evidence.
2. **Short tasks may see little benefit.** A task solved in a few turns will not exercise compaction, rollover, or restart recovery.
3. **Extra state can become noise.** Journal pointers and appendix blocks help only when the model can use them; excessive handoff text can consume the context budget.
4. **Tool semantics and verification matter.** SWE-bench is pass/fail on repository tests; Terminal-Bench uses task-specific scripts. A reliable harness cannot compensate for incorrect edits, weak shell commands, or missing final verification.
5. **Cache metrics are not quality metrics.** Prefix-cache hit rate can improve cost and latency while pass@1 remains unchanged or even falls if stale context is retained incorrectly.
6. **Benchmark variance is material.** OpenAI notes that scaffold settings and a single evaluation seed can change reported results, and that static public tasks have contamination/coverage limits.

## Practical prediction

Relative to the same model with a simple transcript-only harness, LANTU should have its clearest advantage on:

- long multi-step repository changes;
- tasks with large logs or generated files;
- tasks requiring repeated test/fix cycles;
- continuation after compaction or process restart;
- terminal tasks where evidence must be retrieved later.

The expected advantage on ordinary SWE-bench Verified pass@1 is probably modest until retrieval and patch-validation behavior are measured and improved. The expected advantage in trajectory completion, recoverability, token cost, and failure diagnosis is more substantial.

## Measurement plan before making a numeric claim

Run the same model, temperature, tool permissions, timeout, and task set with two harnesses:

1. transcript-only baseline;
2. LANTU with context management enabled.

Record per task:

- pass/fail and test failure category;
- number of model turns and tool calls;
- context tokens, compaction count, and rollover count;
- artifact re-read success;
- restart/recovery success;
- time, token cost, and provider cache statistics;
- whether the final patch was verified before delivery.

Use a small pilot first (for example, 20-30 tasks stratified by expected trajectory length), then a larger fixed sample. Report paired deltas and confidence intervals, not a single anecdotal score. Include ablations for compaction, rollover, artifact persistence, and retrieval so that the harness contribution is not conflated with model or prompt changes.

## Bottom line

LANTU currently looks like a strong reliability-oriented harness rather than a proven high-scoring coding agent. Its architecture addresses failure modes that official terminal and software-engineering benchmarks expose, but the retrieval subsystem and benchmark-scale paired evaluation are necessary before assigning a score forecast. The most credible near-term claim is reduced long-trajectory failure and better cost/recovery behavior; pass-rate gains should be treated as a hypothesis to test.
