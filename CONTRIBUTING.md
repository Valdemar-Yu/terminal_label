# Contributing

Contributions are welcome through GitHub issues and pull requests.

## Development

Requirements:

- Python 3.9 or newer
- Claude Code for strict plugin validation

Run the checks from the repository root:

```bash
python3 -m unittest discover -s tests -v
claude plugin validate --strict .
```

Keep the runtime dependency-free. New code must degrade without interrupting
Claude Code when terminal access, config files, or an existing status line
command are unavailable. Add tests for config migrations and terminal control
sequences.

Do not include transcripts, API keys, local Claude settings, or generated
plugin caches in fixtures.
