# QualAgent

**Methodology-faithful AI agent for qualitative data analysis — with a full audit trail.**

Created by [@bananbenbadr](https://github.com/bananbenbadr)

Open-source, local-first alternative to NVivo's black-box AI: every code assignment comes with a rationale, a confidence score, exact source offsets, and an immutable audit log you can show your reviewers.

[![CI](https://img.shields.io/github/actions/workflow/status/[you]/qualagent/ci.yml)]()
[![PyPI](https://img.shields.io/pypi/v/qualagent)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)]()
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)]()
[![MCP](https://img.shields.io/badge/MCP-server-purple.svg)]()

## Why QualAgent?

- **Framework-faithful, not vibes.** Pluggable methodology packs encode published frameworks — Braun & Clarke thematic analysis, van Leeuwen's social actor taxonomy, open coding — as versioned, citable, inspectable definitions.
- **Defensible by design.** Append-only audit trail records every model call, prompt hash, decision, and human override. Export it as an appendix when reviewers ask "how did the AI do this?"
- **Local-first privacy.** Run fully offline with Ollama. Interview data never leaves your machine unless you explicitly opt into a hosted provider.
- **Human-in-the-loop.** Approve, reject, or edit any code assignment. Your corrections are remembered (vector memory) and applied to future coding.
- **Measurable rigor.** Built-in Cohen's kappa between AI and human-verified coding, so you can report agreement like any inter-rater study.
- **Plays well with agents.** Ships an MCP server — code segments, fetch codebooks, and query audit trails directly from Claude or any MCP client.

## Demo

[30-second GIF: import transcript → run coding → review assignments → export codebook + audit PDF]

## Quickstart (offline, ~5 minutes)

```bash
pip install qualagent
ollama pull qwen3:32b          # or any local model

qualagent init my-study --pack thematic_analysis
cd my-study
qualagent import interviews/*.docx
qualagent code run             # fully local, no API key needed
qualagent review list --status pending
qualagent export codebook --format pdf --out codebook.pdf
qualagent export audit --format pdf --out audit.pdf
```

Using a hosted model instead:

```bash
export QUALAGENT_OPENAI_API_KEY=...
qualagent config set llm.provider openai
qualagent code run --accept-remote   # explicit consent: data leaves your machine
```

## Use it from Claude (MCP)

```json
{
  "mcpServers": {
    "qualagent": {
      "command": "qualagent",
      "args": ["mcp", "--project-dir", "/path/to/my-study/.qualagent"]
    }
  }
}
```

Then ask Claude: *"Code this excerpt with my study's codebook"* or *"Show me all pending assignments below 0.7 confidence."*

## Methodology Packs

| Pack | Framework | Citation |
|---|---|---|
| `open_coding` | Initial/descriptive coding | [Saldana, 2021] |
| `thematic_analysis` | 6-phase thematic analysis | [Braun & Clarke, 2006] |
| `van_leeuwen` | Social actor representation | [van Leeuwen, 2008] |

Author your own: a pack is a versioned YAML — category tree, decision questions, output schema, few-shot examples. See [docs/packs.md](docs/packs.md). Packs without citations are rejected on load.

## What QualAgent is not

- Not a transcription tool (bring your own transcripts).
- Not a replacement for researcher judgment — it drafts, you decide. The review queue is the product.
- Not a hosted SaaS. Your data, your machine.

## Roadmap

Whisper transcription · REFI-QDA (.qdpx) interchange · web review UI · multi-coder support · axial coding pack · multilingual coding (Persian, German, Romanian)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Good first issues are labeled. Pack contributions (new frameworks) are especially welcome — no Python required, just YAML + domain expertise.

## Citation

If QualAgent contributes to published research, please cite: [Zenodo DOI placeholder]

## License

MIT — see [LICENSE](LICENSE).