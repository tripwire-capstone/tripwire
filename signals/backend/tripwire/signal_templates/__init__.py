"""The built-in signal templates: starting points for a new signal on the New signal form.

Each template is two files in this folder, named by its ID:
  <id>.md    the instruction the judge gets, as plain text
  <id>.json  name, short_name, icon, description and output_schema (the fields of each finding)

A signal made from a template is an independent copy: editing it never changes the template, and
changing a template never changes existing signals. The signal only remembers the template's ID.

The wording of all three is Laminar's own (lmnr-ai/lmnr, frontend/components/signals/prompts.ts,
Apache-2.0), so a signal made here judges the same way as Laminar's version of it.
"""

import json
from dataclasses import dataclass
from pathlib import Path

FOLDER = Path(__file__).parent
ORDER = ("issues", "friction", "intent")   # the order the form shows them in


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    short_name: str
    icon: str
    description: str
    instruction: str
    output_schema: list


def load(template_id: str) -> Template:
    meta = json.loads((FOLDER / f"{template_id}.json").read_text())
    instruction = (FOLDER / f"{template_id}.md").read_text().strip()
    return Template(id=template_id, instruction=instruction, **meta)


TEMPLATES = {template_id: load(template_id) for template_id in ORDER}
