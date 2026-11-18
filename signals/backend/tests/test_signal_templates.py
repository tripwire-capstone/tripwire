"""The built-in signal templates: every one is a valid signal definition, and the folder holds
exactly the templates the loader lists. No database needed."""

from tripwire.api.validate import problems
from tripwire.signal_templates import FOLDER, ORDER, TEMPLATES


def test_every_template_is_a_valid_signal_definition():
    for template in TEMPLATES.values():
        assert problems(template.name, template.instruction, template.output_schema, [], None) == [], \
            template.id


def test_the_folder_holds_exactly_the_listed_templates():
    ids = {path.stem for path in FOLDER.iterdir() if path.suffix in (".md", ".json")}
    assert ids == set(ORDER)


def test_templates_keep_laminars_wording():
    assert TEMPLATES["issues"].instruction.startswith("Report every distinct error the agent made")
    assert TEMPLATES["friction"].instruction == (
        "Analyze this session for signs of user frustration or friction.\n"
        "Look for confusion, repeated attempts, or poor user experience.")
    assert [f["choices"] for f in TEMPLATES["friction"].output_schema if f["type"] == "enum"] == [
        ["low", "medium", "high"]]
