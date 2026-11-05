from functools import cache

from dotenv import load_dotenv
from openai import OpenAI

from classifier.config import MODEL

load_dotenv()


@cache
def get_client():
    # Only require credentials when a new score is actually needed.
    return OpenAI()


def classify(item):
    turns = item["turns"]
    messages = []

    # Earlier turns provide context, like in the manual evaluation.
    for turn in turns[:-1]:
        messages.append({"role": "user", "content": turn["user"]})
        if turn["reply"]:
            messages.append({"role": "assistant", "content": turn["reply"]})

    # Judge the current user message; omit its assistant reply and tool calls.
    messages.append({"role": "user", "content": turns[-1]["user"]})

    return get_frustration_probability(messages)


def get_frustration_probability(messages):
    decision = get_client().decisions.create(
        model=MODEL,
        input=messages,
        questions=[
            {
                "type": "predicate",
                "name": "user_frustrated",
                "instructions": (
                    "Is the user's latest message expressing frustration, "
                    "irritation, dissatisfaction, impatience, or giving up "
                    "because of the interaction? Use earlier messages only "
                    "as context. Do not treat a neutral correction or a "
                    "positive message as frustration."
                ),
            }
        ],
    )

    answer = decision.answers[0]
    if answer.type != "predicate":
        raise RuntimeError(f"Expected a probability answer, got {answer.type}")

    return answer.probability
