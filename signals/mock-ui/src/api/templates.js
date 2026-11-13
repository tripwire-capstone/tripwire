// The signal templates (hidden until templates are built: FEATURES.templates, HIDDEN_CONTROLS.md).
// The team's six Laminar templates, kept here so the form can show them when the flag comes back.

// Creation presets mirror the approved Laminar template content. They are not live tripwires.
// Source: lmnr-ai/lmnr, frontend/components/signals/prompts.ts (Apache-2.0).
export const TEMPLATES = [
  {
    "id": "failure",
    "name": "Failure Detector",
    "shortName": "Failure",
    "icon": "alert-circle",
    "description": "Spot errors, loops and wrong tool usage",
    "instruction": "Report every distinct error the agent made in this run that meets the bar below. One finding per distinct error. [IMPORTANT]: If nothing meets the bar, submit no findings. A clean run is a valid outcome, and an unsupported finding is worse than no finding.\n\nReportable:\n1. A wrong action or flawed reasoning step that affected the outcome or wasted substantial work.\n2. A violation of a stated instruction or precondition — from the system prompt, the user's request, or a tool's stated contract. Reportable even if the run continued past it and even if the agent later corrected it. An instruction here is an explicit requirement or prohibition; a description of a typical procedure, a suggestion, or general guidance ('keep changes minimal', 'test your work', 'you may use X') is not one, and departing from it is reportable only under rule 1 by its effect. A violation is a forbidden action the agent actually performed, or a required action or check it never performed; an attempt the environment rejected before it took effect, which the agent then corrected, is a slip under rule 5, not a violation. Formatting or wording deviations count when they are in the final answer or deliverable, or when the instruction states the format is required; the same deviation in intermediate reasoning or scratch output does not.\n3. Repeated near-identical failures (same tool, same arguments, same kind of error) that together consumed a substantial part of the run, even if the agent eventually recovered. Report these as ONE finding: state how many times it happened and cite the spans.\n4. A factual assurance made to the user that no tool result or provided context supports. Unsupported is enough; you do not need an in-trace contradiction for the agent's own claims. This includes an unqualified answer when the trace shows the agent saw conflicting sources, an alternative reading, or an unresolved check that would change it — the missing caveat is the error, whichever option the agent picked — and a claim about every item or the whole set when only a sample was checked.\n\nNot reportable:\n5. A single minor slip the agent corrected within a step or two. Rule 2 overrides this exemption: an instruction violation is reportable even when minor and corrected.\n6. Skipping a planned lookup or verification, when the evidence the agent did gather is sufficient to support its answer. Rule 2 overrides this too: if the skipped step was itself a stated instruction, report it. So does rule 4: if the agent told the user the check was done, or its answer asserts something only the skipped step could establish (completeness of a search, that every item was checked), report it.\n\nGrounding:\n7. Cite the specific spans and quote the decisive evidence for every finding.\n8. The agent's own reasoning is always in scope: flag a conclusion the trace's evidence doesn't support. This requires no external knowledge, only reading the trace.\n9. External facts are different: never declare a result wrong from your own knowledge of the world unless the contradiction is visible in the trace itself. Never assert mechanical details the trace does not literally show — when judging \"substantial\" under rules 1 and 3, count the spans or tool calls you can actually see and cite them.\n10. Never infer environment rules or prerequisites that are not stated. This does not limit rule 4: the agent's assurances still need support.",
    "payloadFields": [
      {
        "name": "description",
        "description": "What went wrong, the decisive evidence with span references, and the impact on the run's outcome",
        "type": "string",
        "choices": []
      }
    ]
  },
  {
    "id": "logic",
    "name": "Logic Analyzer",
    "shortName": "Logic",
    "icon": "brain",
    "description": "Find flaws in reasoning and decision-making",
    "instruction": "Analyze this trace for flaws in the agent's reasoning or decision-making.\nLook for poor planning, contradictions, unnecessary steps, or missed opportunities.",
    "payloadFields": [
      {
        "name": "description",
        "description": "Description of the reasoning flaw or issue",
        "type": "string",
        "choices": []
      },
      {
        "name": "severity",
        "description": "Severity of the logic issue",
        "type": "enum",
        "choices": [
          "low",
          "medium",
          "high"
        ]
      }
    ]
  },
  {
    "id": "task",
    "name": "Task Evaluator",
    "shortName": "Task",
    "icon": "check-circle",
    "description": "Evaluate if the agent completed the user's request",
    "instruction": "Did the agent successfully complete what the user asked for?\nConsider whether the output matches the user's intent and is correct.",
    "payloadFields": [
      {
        "name": "success",
        "description": "Level of task completion",
        "type": "enum",
        "choices": [
          "full",
          "partial",
          "failed"
        ]
      },
      {
        "name": "description",
        "description": "Description of task completion status. If the task was not completed, describe what went wrong.",
        "type": "string",
        "choices": []
      }
    ]
  },
  {
    "id": "friction",
    "name": "Friction Detector",
    "shortName": "User friction",
    "icon": "frown",
    "description": "Identify user frustration and poor UX",
    "instruction": "Analyze this session for signs of user frustration or friction.\nLook for confusion, repeated attempts, or poor user experience.",
    "payloadFields": [
      {
        "name": "description",
        "description": "Description of the friction or frustration observed",
        "type": "string",
        "choices": []
      },
      {
        "name": "severity",
        "description": "Severity of the user friction",
        "type": "enum",
        "choices": [
          "low",
          "medium",
          "high"
        ]
      }
    ]
  },
  {
    "id": "hallucination",
    "name": "Hallucination Detector",
    "shortName": "Hallucination",
    "icon": "cloud-off",
    "description": "Detect made-up facts and incorrect claims",
    "instruction": "Did the agent make up facts, hallucinate information, or confidently\nstate something incorrect? Compare claims against available context.",
    "payloadFields": [
      {
        "name": "description",
        "description": "Description of the hallucination",
        "type": "string",
        "choices": []
      },
      {
        "name": "type",
        "description": "Type of hallucination detected",
        "type": "enum",
        "choices": [
          "factual",
          "contextual",
          "fabrication"
        ]
      }
    ]
  },
  {
    "id": "intent",
    "name": "Intent Classifier",
    "shortName": "Intent",
    "icon": "target",
    "description": "Classify what the user was trying to accomplish",
    "instruction": "What was the user trying to accomplish in this session?\nClassify the primary intent.",
    "payloadFields": [
      {
        "name": "intent",
        "description": "The user's primary intent",
        "type": "string",
        "choices": []
      },
      {
        "name": "complexity",
        "description": "Complexity of the user's request",
        "type": "enum",
        "choices": [
          "simple",
          "moderate",
          "complex"
        ]
      }
    ]
  }
];

