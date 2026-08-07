"""Centralized prompts for Kestrel final post generation."""

from __future__ import annotations

import json

from app.services.final_post.models import FinalPostContext

FINAL_POST_SYSTEM_PROMPT = """You are Kestrel, an AI Security persona.

Mission:
Identify and communicate meaningful, current AI-security developments with evidence-first reasoning.

You should:
- prioritize current developments
- prefer strong evidence
- distinguish facts from interpretation
- remain technically credible
- avoid generic AI content
- avoid unsupported claims
- avoid hype
- maintain consistent identity
- maintain domain relevance
- allow evidence-backed belief changes
- use only supplied research sources

You must NOT:
- fabricate facts, statistics, quotations, or URLs
- invent sources
- make unsupported claims
- pretend to have information not supplied
- mention internal architecture, scoring, self-debate, memory systems,
  consistency guards, pipeline stages, being an autonomous agent,
  or this competition/evaluation
- write generic motivational content

Write a natural social-media-style post, not a technical pipeline report.
Return structured JSON only.
"""


def build_final_post_user_prompt(context: FinalPostContext) -> str:
    return (
        f"Persona: {context.persona_name}\n"
        f"Domain: {context.persona_domain}\n"
        f"Allowed source URLs (copy exactly when citing): "
        f"{json.dumps(context.grounded_urls, ensure_ascii=True)}\n"
        f"Selected topic: {json.dumps(context.topic, ensure_ascii=True)}\n"
        f"Debate summary: {json.dumps(context.debate, ensure_ascii=True)}\n"
        f"Consistency summary: {json.dumps(context.consistency, ensure_ascii=True)}\n"
        f"Relevant memories: {json.dumps(context.memories[:8], ensure_ascii=True)}\n"
        f"Rejected/lower alternatives: {json.dumps(context.alternatives[:5], ensure_ascii=True)}\n"
        f"Research context: {json.dumps(context.research[:8], ensure_ascii=True)}\n"
        f"Recent prior titles: {json.dumps(context.previous_titles[:10], ensure_ascii=True)}\n"
        "Story/continuity context (optional). Use naturally if relevant; "
        "do not mention internal pipeline terms like contradiction or scoring.\n"
        f"Story context: {json.dumps(context.intelligence or {}, ensure_ascii=True)}\n\n"
        "Produce JSON:\n"
        "{"
        '"text":"concise evidence-driven AI Security post",'
        '"rationale":"explain why selected, why now, persona relevance, evidence, '
        'alternatives, memory influence if any, consistency alignment",'
        '"source_urls":["..."]'
        "}\n"
        "Rules:\n"
        "- text must be grounded in supplied research\n"
        "- rationale must explain selection using pipeline evidence, not chain-of-thought\n"
        "- source_urls must be copied from allowed URLs only\n"
        "- no hype, no fabricated citations\n"
    )


def build_repair_user_prompt(
    context: FinalPostContext,
    *,
    previous: dict,
    validation_errors: list[str],
) -> str:
    return (
        f"Previous draft failed validation.\n"
        f"Validation issues: {json.dumps(validation_errors, ensure_ascii=True)}\n"
        f"Previous draft: {json.dumps(previous, ensure_ascii=True)}\n\n"
        f"{build_final_post_user_prompt(context)}\n"
        "Repair the draft:\n"
        "- remove unsupported claims and hype\n"
        "- remove fabricated URLs\n"
        "- improve AI Security domain relevance\n"
        "- preserve grounded facts only\n"
        "- do NOT invent new evidence\n"
    )
