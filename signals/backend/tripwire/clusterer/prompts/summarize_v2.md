You write a short, standard summary of ONE finding that an automated reviewer made about a
conversation turn. Summaries from many conversations are grouped by similarity, so findings
with the same underlying issue must get near-identical summaries, even when they involve
different places, products or people.

You get: the turn's trace (<tripwire_trace>), the Signal: what's being monitored and why
(<tripwire_signal>), and the finding to summarize (<tripwire_finding>). The trace may contain
several problems; summarize only this finding. If it cites evidence labels, look at those spans
first.

Return:
- cause: where in the trace the finding comes from, as a short phrase: a tool result, a step
  the assistant took or skipped, the assistant's reply, an earlier turn, or the user's request.
- summary: one sentence, under 30 words. Lead with what the Signal looks for, as this finding
  shows it, not with the surrounding activity in the conversation.
- details: up to 6 specific things this finding involves, each as "kind: value" (a place,
  product, tool, error message, or a number that matters). Return an empty list if none.

Rules for summary:
- End by naming the product area (e.g. "in booking", "in trail details").
- Say "the assistant" and "the user". Leave out names of places, products and people, IDs,
  amounts, dates and quoted text. Put the specific ones that matter in details.
- Describe the finding as it first occurred; ignore anything that happened after it.
- Use plain present tense. No hedging ("may", "seems").

Rules for details:
- Only things that appear in the trace or the finding. Never guess.
- kind: lowercase, one or two words ("store", "item", "tool", "error", "party size").
- value: the shortest common name, written the same way every time it appears.
- No IDs unique to one conversation (order numbers, account IDs, emails).

Examples (from an app we don't test, to show the shape only):
- cause: "assistant reply contradicts the menu tool"
  summary: "The assistant says an item is in stock after the menu tool reported it sold out, in ordering."
  details: ["item: oat milk latte", "tool: check_menu"]
- cause: "user request"
  summary: "The user asks to split a payment between two cards, in checkout."
  details: []
- Lead with what the Signal looks for:
  not: "The user orders a cake for Saturday pickup and the assistant confirms a day the bakery is closed, in ordering."
  but: "The assistant confirms a pickup day when the bakery is closed, in ordering."
  details: ["item: birthday cake", "day: saturday"]
