# GLM-5.2 Tokenizer and Cache Counting

## Findings

- The official Hugging Face repository for GLM-5.2 is `zai-org/GLM-5.2`.
- Its file list contains `tokenizer.json`, `tokenizer_config.json`, and `chat_template.jinja`.
- The official tokenizer configuration reports `tokenizer_class: TokenizersBackend` and `model_max_length: 1048576`.
- Therefore, GLM-5.2 does have an official tokenizer. The earlier explanation that it had no tokenizer was incorrect.

## Implication for LANTU

- `characters / 3.5` is only LANTU's generic fallback estimate. It is not caused by GLM-5.2 lacking a tokenizer.
- Offline GLM-5.2 token estimates should use the official tokenizer when it is available.
- A 64-token cache block rule must not be copied from another provider without confirming that provider's cache granularity.
- For real BaiLian measurements, prefer the usage fields returned by the API, especially prompt tokens and cached prompt tokens, when available.

## Sources

- https://huggingface.co/zai-org/GLM-5.2
- https://huggingface.co/zai-org/GLM-5.2/raw/main/tokenizer_config.json
