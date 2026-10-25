---
name: mathematics
description: Summary of all skills for solving hard and competition mathematics —
  olympiad problem solving (IMO, Putnam, USAMO, AIME) with adversarial verification,
  and sustained proof work in both multi-agent siege rounds and single-session solo
  mode. Use when asked to solve a math problem or proof, work a competition problem,
  settle whether a conjecture holds, or drive a hard mathematical question to a proved
  or refuted answer.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: math-olympiad
    name: math-olympiad
    license: unknown
  - owner: math-proof
    name: siege
    license: unknown
  - owner: math-proof
    name: solo
    license: unknown
---

# Mathematics: Problem Solving & Proof

The distinguishing idea across these skills: **never trust a single
derivation.** Every answer is checked by an independent pass, because a
plausible-looking proof is exactly what a wrong proof looks like.

## Constituent skills (3)

| Skill | Origin | What it contributes |
|---|---|---|
| math-olympiad | math-olympiad | Solves competition math (IMO, Putnam, USAMO, AIME) with adversarial verification |
| math-proof/siege | math-proof | Hard math in rounds: a judge poses questions, independent workers answer, judge keeps a ledger of proved/refuted/open |
| math-proof/solo | math-proof | Hard math in one session, no sub-agents; staged reasoning recorded to a notes file, ending in a self-review |

## Consolidated guidance

1. **Adversarial verification is the core method.** Produce the proof,
   then actively try to refute it. Agreement from a fresh, skeptical pass
   is the signal — not confidence from the author.
2. **Keep a ledger.** `siege`: track every claim as proved / refuted /
   open. An untracked claim set loses track of what was actually
   established.
3. **Write reasoning down as you go.** `solo`: staged reasoning in a notes
   file lets the final self-review check the chain rather than the
   conclusion.
4. **Separate the judge from the workers.** The one who poses and grades
   must not be the one who produced the answer; otherwise errors are
   graded by the same bias that made them.
5. **Restate the problem precisely before solving.** Most "hard" problems
   are ambiguous readings. Fix the exact statement, domains and
   quantifiers first.
6. **Small counterexamples beat long arguments.** For universal claims,
   spend real effort on a counterexample before investing in a proof.
7. **Choose the mode by the problem.** Competition problems with a known
   answer type → `math-olympiad`. Open-ended, multi-part, long-horizon →
   `siege`. Contained but subtle, no fan-out needed → `solo`.
8. **State the answer's status honestly.** Proved, refuted, or still open
   — "looks right" is not a status.

## When to use this skill

"Solve this problem", "prove or disprove…", "this is an IMO/Putnam-style
problem", "is this conjecture true?", "check my proof".

## Related

Typesetting of mathematical documents (LaTeX) is grouped under
`documents-and-knowledge-artifacts`.

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| math-olympiad | math-olympiad | unknown |
| math-proof | siege | unknown |
| math-proof | solo | unknown |
