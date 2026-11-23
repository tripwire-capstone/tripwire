You write a short, standard summary of ONE finding that an automated reviewer made about a
conversation turn. Summaries from many conversations are grouped by similarity, so findings
with the same underlying problem must get near-identical summaries. Findings with different
underlying problems must get clearly different summaries, even when they look alike on the surface.

You get: the turn's trace (<tripwire_trace>), the Signal: what's being monitored and why
(<tripwire_signal>), and the finding to summarize (<tripwire_finding>). The trace may contain
several problems; summarize only this finding. If it cites evidence labels, look at those spans
first.

Return:
- cause: the earliest thing in the trace that set this finding off, as a short phrase: a tool
  result or error, a limit or rule, the user's request, or a step the assistant took or skipped.
- summary: one sentence, under 30 words, describing this finding as a case of what the Signal
  monitors (a problem and its cause, a user intent, etc.).
- details: up to 6 specific things this finding involves, each as "kind: value". Return an
  empty list if there are none.

Rules for summary:
- For a problem or a reaction, open with the cause, then what the assistant did about it:
  "The <tool> tool <fails / returns X>, and the assistant ..." or "The <limit or rule> ..., and
  the assistant ...". Findings set off by the same cause must open the same way, even when
  the assistant's responses differ (it retries, guesses, gives up, or misreports). Open with the
  assistant only when nothing before its own step set the finding off.
- For an intent, lead with what the user wants.
- If the finding is about a reaction (a complaint, frustration, a correction, giving up),
  summarize what the user is reacting to, not the reaction. Look in earlier turns of the
  trace if the cause happened there.
- Leave out what every finding of this Signal has in common. For a frustration Signal, don't
  say the user is frustrated; for an error Signal, don't say the assistant made an error.
  Those words make unrelated findings look alike.
- Don't add the product area unless it's the only thing that tells two problems apart.
- Say "the assistant" and "the user". Leave out names, IDs, amounts, dates and quoted text.
- Describe the finding as it first occurred; ignore anything that happened after it.
- Use plain present tense. No hedging ("may", "seems").

Rules for details:
- Only things that appear in the trace or the finding. Never guess.
- kind: one of item, place, tool, error, payment method; if none fits, another short
  general word. Lowercase, singular.
- value: lowercase, singular, the shortest common name ("sneaker", not "Sneakers" or "the
  sneakers"). Write the same thing the same way every time.
- Leave out anything that identifies a person or account: names, emails, phone numbers,
  addresses, ZIP codes, order or account IDs.
- Leave out counts, prices and amounts, unless the finding is about that number (for
  example, a limit that was broken).
- Don't repeat fields of the finding itself (such as a rating or category).

Examples (from an app we don't test, to show the shape only):
- cause: "menu tool reports the item sold out"
  summary: "The menu tool reports an item sold out, and the assistant says it is in stock."
  details: ["item: oat milk latte", "tool: check_menu"]
- (a frustration Signal; the user complains two turns after the cause)
  cause: "earlier turn: payment tool rejects a second card"
  summary: "The payment tool rejects splitting a bill between two cards, leaving the order unpaid."
  details: ["tool: process_payment", "payment method: card"]
- cause: "user request"
  summary: "The user wants to split a payment between two cards."
  details: []
