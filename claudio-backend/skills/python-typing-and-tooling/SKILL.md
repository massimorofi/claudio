---
name: python-typing-and-tooling
description: Summary of all skills for Python typing, refactoring, environment
  correctness and safe monkey-patching — Pylance refactoring, writing type stubs,
  adding type annotations, type inference, fact-grounded coding, adding or dropping
  Python version support, discovering Python managers, cross-platform path handling,
  Python profiling, and patching Python methods without self-recursion. Use when
  asked to add or fix Python types, write .pyi stubs, refactor Python safely, support
  or drop a Python version, find the right Python/venv manager, fix path-separator
  bugs, profile Python performance, or monkey-patch a method without causing infinite
  recursion.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: Copilot CLI
    name: pylance-refactoring
    license: unknown
  - owner: Copilot CLI
    name: python-write-stubs
    license: unknown
  - owner: Copilot CLI
    name: python-add-type-annotations
    license: unknown
  - owner: Copilot CLI
    name: python-type-inference
    license: unknown
  - owner: Copilot CLI
    name: python-fact-grounded-coding
    license: unknown
  - owner: Copilot CLI
    name: add-python-version-support
    license: unknown
  - owner: Copilot CLI
    name: drop-python-version-support
    license: unknown
  - owner: MS Python ext
    name: python-manager-discovery
    license: unknown
  - owner: MS Python ext
    name: cross-platform-paths
    license: unknown
  - owner: MS Python ext
    name: pylance-python-profiling
    license: unknown
  - owner: local
    name: python-monkey-patching
    license: unknown
---

# Python Typing, Refactoring & Environment Correctness

Two clusters that share a root cause: **Python's runtime flexibility hides
mistakes that static typing and explicit environment handling expose.**

## Constituent skills (10)

| Skill | Origin | What it contributes |
|---|---|---|
| pylance-refactoring | Copilot CLI | Refactoring with Pylance's semantic understanding |
| python-write-stubs | Copilot CLI | Authoring `.pyi` stub files for untyped libraries |
| python-add-type-annotations | Copilot CLI | Adding annotations to existing code |
| python-type-inference | Copilot CLI | Reading/deriving what the checker infers |
| python-fact-grounded-coding | Copilot CLI | Code against verified facts about the library, not assumed APIs |
| add-python-version-support | Copilot CLI | Widening supported versions |
| drop-python-version-support | Copilot CLI | Removing old versions cleanly |
| python-manager-discovery | MS Python ext | Finding the installed Python/venv/conda managers |
| cross-platform-paths | MS Python ext | Path handling that works on Windows/macOS/Linux |
| pylance-python-profiling | MS Python ext | Performance profiling of Python code |

## Consolidated guidance

1. **Annotations are checked, not decorative.** After adding types, run the
   checker. Annotations that produce errors you "know are fine" are how type
   checking gets disabled.
2. **Infer before you annotate.** `python-type-inference`: know what the
   checker already derives; annotate only where inference is wrong or the
   public surface needs it. Redundant annotations are noise.
3. **Stubs are contracts.** A `.pyi` stub that lies is worse than no stub —
   it makes the checker confidently wrong. Verify stub signatures against
   the real runtime.
4. **Ground claims in the installed version.** `python-fact-grounded-coding`:
   check the actual installed library's API and version rather than
   remembered signatures; Python APIs drift between releases.
5. **Version changes touch config, not just code.** Adding/dropping a
   version means `requires-python`, CI matrix, classifiers, tox/venv config
   and dependency pins. Change all of it or the claim is false.
6. **Never assume the interpreter.** `python-manager-discovery`: locate the
   actual interpreter/venv/conda environment; "works on my Python" is the
   most common non-bug.
7. **Paths are a portability bug by default.** Use `pathlib` and joining,
   never string concatenation with `/` or `\`; resolve, don't assume cwd.
8. **Profile before optimizing.** `pylance-python-profiling`: measure first;
   the bottleneck is never where intuition said it was.

## When to use this skill

"Add types to this module", "write a stub for this library", "refactor this
Python", "support Python 3.13 / drop 3.8", "which Python is being used?",
"this breaks on Windows", "why is this slow?".

## Monkey-Patching Without Self-Recursion

Merged from the local `python-monkey-patching` skill. When you replace a Python
method with a wrapper, the wrapper must call the *original* implementation, not the
patched one, or it recurses forever.

### What to check
- If a patched method raises `RecursionError: maximum recursion depth exceeded`, inspect the traceback for repeated calls to the same wrapper function.
- Confirm the wrapper is calling the original method, not the patched method again.
- Verify the running process has actually loaded the corrected source file; stale processes can keep executing old code.
- Check async or streaming paths separately, because recursion may only appear under real request flow, not static tests.

### What usually goes wrong
A common mistake is replacing a method with a wrapper that accidentally calls the patched method instead of the original implementation. This creates an infinite recursive loop.

Example of a bad pattern:

```python
def patched_method(self, *args, **kwargs):
    # BUG: calls the patched method again
    return self._response_to_llm_response(*args, **kwargs)
```

If `patched_method` has already replaced `_response_to_llm_response`, this calls itself forever.

### Fix that works
Save the original function before patching, then call that saved function inside the wrapper.

```python
_ORIGINAL_RESPONSE_TO_LLM_RESPONSE = SomeClass._response_to_llm_response

def patched_response_to_llm_response(self, *args, **kwargs):
    result = _ORIGINAL_RESPONSE_TO_LLM_RESPONSE(self, *args, **kwargs)
    # add custom behavior here
    return result

SomeClass._response_to_llm_response = patched_response_to_llm_response
```

After applying the fix:
- Restart the backend process so the corrected code is loaded.
- Confirm the process is running the updated file, not a stale module or old deployment.
- Re-run the failing request and check that the recursion traceback is gone.

### Quick checklist
- [ ] Original function saved before patching
- [ ] Wrapper calls the saved original, not the patched method
- [ ] No recursive call chain in traceback
- [ ] Backend restarted after patch
- [ ] Corrected code confirmed loaded in running process

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| Copilot CLI | pylance-refactoring | unknown |
| Copilot CLI | python-write-stubs | unknown |
| Copilot CLI | python-add-type-annotations | unknown |
| Copilot CLI | python-type-inference | unknown |
| Copilot CLI | python-fact-grounded-coding | unknown |
| Copilot CLI | add-python-version-support | unknown |
| Copilot CLI | drop-python-version-support | unknown |
| MS Python ext | python-manager-discovery | unknown |
| MS Python ext | cross-platform-paths | unknown |
| MS Python ext | pylance-python-profiling | unknown |
| local | python-monkey-patching | unknown |
