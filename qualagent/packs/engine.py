"""Pack engine: render a pack into prompts and expose its output schema."""

from __future__ import annotations

import json
from dataclasses import dataclass

from qualagent.domain.models import Code
from qualagent.packs.loader import Pack


@dataclass(frozen=True)
class RenderedPrompt:
    """A fully rendered coding prompt."""

    system: str
    user: str


class PackEngine:
    """Turn pack data + codebook + memory into deterministic prompts."""

    def __init__(self, pack: Pack) -> None:
        self.pack = pack

    def render_system_prompt(self, codes: list[Code] | None = None) -> str:
        """Render the pack's system prompt: instructions, tree, questions, few-shots.

        Args:
            codes: Current codebook codes whose definitions are injected so the
                model codes against the study's evolving definitions.
        """
        parts: list[str] = [self.pack.coding_instructions]
        parts.append(self._render_tree())
        if self.pack.decision_questions:
            parts.append(
                "Decision questions (answer in order):\n"
                + "\n".join(f"- {q}" for q in self.pack.decision_questions)
            )
        if codes:
            parts.append(
                "Current codebook definitions:\n"
                + "\n".join(self._render_code(code) for code in codes)
            )
        parts.append(f"Citation for this framework: {self.pack.citation}")
        parts.append(
            "Output rules: respond with ONLY a JSON object matching the schema. "
            "`rationale` must cite the segment text. `confidence` is in [0,1]. "
            + (
                "Multiple codes per segment are allowed."
                if self.pack.multi_code
                else "Assign exactly one code per segment."
            )
        )
        return "\n\n".join(parts)

    def _render_tree(self) -> str:
        lines = ["Category taxonomy (most specific applicable category wins):"]
        descriptions = self.pack.category_descriptions()
        for path in self.pack.category_paths():
            leaf = path.rsplit(".", 1)[-1]
            desc = descriptions.get(path, "")
            lines.append(f"- {leaf} ({path}): {desc}" if desc else f"- {path}")
        return "\n".join(lines)

    def _render_code(self, code: Code) -> str:
        bits = [f"- {code.name}: {code.definition}"]
        if code.inclusion_criteria:
            bits.append(f"  include: {code.inclusion_criteria}")
        if code.exclusion_criteria:
            bits.append(f"  exclude: {code.exclusion_criteria}")
        return "\n".join(bits)

    def render_user_prompt(
        self,
        segments: list[tuple[str, str, str | None]],
        memories: list[str] | None = None,
    ) -> str:
        """Render the batch user prompt.

        Args:
            segments: (segment_id, text, speaker) triples.
            memories: Retrieved memory strings (corrections, definitions).
        """
        parts: list[str] = []
        if memories:
            parts.append(
                "Previously recorded user corrections (apply these):\n"
                + "\n".join(f"- {m}" for m in memories)
            )
        parts.append("Code the following segments:")
        for seg_id, text, speaker in segments:
            speaker_prefix = f"[{speaker}] " if speaker else ""
            parts.append(f"ID {seg_id}: {speaker_prefix}{text}")
        parts.append(
            'Return JSON: {"results": [{"segment_id", "assignments": '
            '[{"code", "rationale", "confidence"}], "uncodable"}], '
            '"new_code_proposals": [...]}. Use leaf category names or existing '
            "codebook names as `code`. Propose new codes only when nothing fits."
        )
        return "\n".join(parts)

    def render_few_shots(self) -> str:
        """Render few-shot examples as JSON blocks."""
        if not self.pack.few_shot_examples:
            return ""
        blocks = []
        for example in self.pack.few_shot_examples:
            blocks.append(
                "Input: "
                + json.dumps(example.get("input", ""), ensure_ascii=False)
                + "\nOutput: "
                + json.dumps(example.get("output", []), ensure_ascii=False)
            )
        return "\n\n".join(blocks)

    @property
    def output_schema(self) -> dict[str, object]:
        """The pack's declared output schema (provider-native structured output)."""
        return self.pack.output_schema
