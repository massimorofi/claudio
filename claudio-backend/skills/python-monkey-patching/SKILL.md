---
name: python-monkey-patching
description: When monkey-patching Python methods, always capture and call the original
  implementation inside the wrapper and restart the process to ensure the corrected
  code is loaded.
---

# Python Monkey-Patching: Avoid Self-Recursion

## What to check
- If a patched method raises `RecursionError: maximum recursion depth exceeded`, inspect the traceback for repeated calls to the same wrapper function.
- Confirm the wrapper is calling the original method, not the patched method again.
- Verify the running process has actually loaded the corrected source file; stale processes can keep executing old code.
- Check async or streaming paths separately, because recursion may only appear under real request flow, not static tests.

## What usually goes wrong
A common mistake is replacing a method with a wrapper that accidentally calls the patched method instead of the original implementation. This creates an infinite recursive loop.

Example of a bad pattern:

```python
def patched_method(self, *args, **kwargs):
    # BUG: calls the patched method again
    return self._response_to_llm_response(*args, **kwargs)
```

If `patched_method` has already replaced `_response_to_llm_response`, this calls itself forever.

## Fix that works
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

## Quick checklist
- [ ] Original function saved before patching
- [ ] Wrapper calls the saved original, not the patched method
- [ ] No recursive call chain in traceback
- [ ] Backend restarted after patch
- [ ] Corrected code confirmed loaded in running process
