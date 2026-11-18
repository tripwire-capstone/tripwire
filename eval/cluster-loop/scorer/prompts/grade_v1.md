PLACEHOLDER RUBRIC (not yet approved by the user; wording may change before the scorer is frozen).

You grade the name of one cluster of events from an LLM observability tool. Each event was flagged by a Signal (for example "Issues": something went wrong in a conversation with an AI agent) and summarized in one or two sentences. A cluster groups events that share a cause; its name is what an engineer sees in a list.

You are shown the cluster's name, the summaries of the members closest to its center, the summaries of its farthest members, and the names of the other clusters of the same Signal.

Score three criteria, each 0, 1 or 2:

- cause: does the name state the shared underlying cause, not just a broad theme or the symptom? 2 = names a specific cause an engineer could act on; 1 = a theme or a symptom that points toward the cause; 0 = vague, generic or wrong.
- fit: does the name fit all members shown, including the farthest ones? 2 = every member shown fits; 1 = most fit, a few don't; 0 = many members don't fit.
- distinct: is the name clearly different from the other clusters' names? 2 = clearly distinct; 1 = overlaps one sibling's meaning; 0 = could be swapped with a sibling's name.

Judge only from what you are shown. Answer with the three integers.
