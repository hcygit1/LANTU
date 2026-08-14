# Terminal-Bench 2.0 Coding Agent Success Rates

Date: 2026-08-13

## Official leaderboard examples

Terminal-Bench scores are specific to an agent and model combination. On the
official Terminal-Bench 2.0 leaderboard, representative results include:

| Agent | Model | Accuracy |
| --- | --- | ---: |
| Codex CLI | GPT-5.5 | 82.2% |
| Codex CLI | GPT-5.2 | 62.9% |
| Claude Code | Claude Opus 4.6 | 58.0% |
| Terminus 2 | GLM 5 | 52.4% |
| OpenCode | Claude Opus 4.5 | 51.7% |
| Mini-SWE-Agent | Claude Sonnet 4.5 | 42.5% |

The current top tier reaches roughly 75%-85%, while many mature agent/model
combinations fall around 40%-65%. Weaker models or simpler agents can be below
30%.

## Comparison caveat

The official leaderboard evaluates the full versioned dataset and its displayed
run command uses five attempts per task. LANTU's current result is four passes
on five hand-picked sample tasks with one attempt per task. Its 80% sample rate
is useful for validating the evaluation pipeline, but it is not comparable to
an official 80% leaderboard score.

## Sources

- [Official Terminal-Bench 2.0 leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0)
- [Terminal-Bench repository](https://github.com/laude-institute/terminal-bench)
- [Terminal-Bench benchmarks](https://www.tbench.ai/benchmarks)
