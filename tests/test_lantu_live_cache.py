from bench.lantu_live_cache import summarize_usage


def test_summarize_usage_uses_provider_cached_tokens() -> None:
    result = summarize_usage(
        [
            {"input_tokens": 100, "cache_read_tokens": 0, "output_tokens": 4},
            {"input_tokens": 20, "cache_read_tokens": 80, "output_tokens": 5},
        ]
    )

    assert result == {
        "model_requests": 2,
        "prompt_tokens": 200,
        "cached_tokens": 80,
        "uncached_tokens": 120,
        "output_tokens": 9,
        "overall_cache_hit_rate": 0.4,
        "warm_cache_hit_rate": 0.8,
    }


def test_summarize_usage_handles_empty_provider_response() -> None:
    result = summarize_usage([])

    assert result["model_requests"] == 0
    assert result["prompt_tokens"] == 0
    assert result["cached_tokens"] == 0
    assert result["overall_cache_hit_rate"] == 0.0
    assert result["warm_cache_hit_rate"] == 0.0
