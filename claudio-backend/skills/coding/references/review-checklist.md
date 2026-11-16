# Code review checklists & feedback



## Review Checklist

### Identifying Problems

Look for these issues in code changes:

- **Runtime errors**: Potential exceptions, null pointer issues, out-of-bounds access
- **Performance**: Unbounded O(n²) operations, N+1 queries, unnecessary allocations
- **Side effects**: Unintended behavioral changes affecting other components
- **Backwards compatibility**: Breaking API changes without migration path
- **ORM queries**: Complex Django ORM with unexpected query performance
- **Security vulnerabilities**: Injection, XSS, access control gaps, secrets exposure

### Design Assessment

- Do component interactions make logical sense?
- Does the change align with existing project architecture?
- Are there conflicts with current requirements or goals?

### Test Coverage

Every PR should have appropriate test coverage:

- Functional tests for business logic
- Integration tests for component interactions
- End-to-end tests for critical user paths

Verify tests cover actual requirements and edge cases. Avoid excessive branching or looping in test code.

### Long-Term Impact

Flag for senior engineer review when changes involve:

- Database schema modifications
- API contract changes
- New framework or library adoption
- Performance-critical code paths
- Security-sensitive functionality

## Feedback Guidelines

### Tone

- Be polite and empathetic
- Provide actionable suggestions, not vague criticism
- Phrase as questions when uncertain: "Have you considered...?"

### Approval

- Approve when only minor issues remain
- Don't block PRs for stylistic preferences
- Remember: the goal is risk reduction, not perfect code

## Common Patterns to Flag

### Python/Django

```python
# Bad: N+1 query
for user in users:
    print(user.profile.name)  # Separate query per user

# Good: Prefetch related
users = User.objects.prefetch_related('profile')
```

### TypeScript/React

```typescript
// Bad: Missing dependency in useEffect
useEffect(() => {
  fetchData(userId);
}, []);  // userId not in deps

// Good: Include all dependencies
useEffect(() => {
  fetchData(userId);
}, [userId]);
```

### Security

```python
# Bad: SQL injection risk
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# Good: Parameterized query
cursor.execute("SELECT * FROM users WHERE id = %s", [user_id])
```

## 🎯 Review Output Format

### Issue Template
```

## [PRIORITY] [CATEGORY]: [Brief Description]

**Location**: [Table/View/Procedure name and line number if applicable]
**Issue**: [Detailed explanation of the problem]
**Security Risk**: [If applicable - injection risk, data exposure, etc.]
**Performance Impact**: [Query cost, execution time impact]
**Recommendation**: [Specific fix with code example]

**Before**:
```sql
-- Problematic SQL
```

**After**:
```sql
-- Improved SQL
```

**Expected Improvement**: [Performance gain, security benefit]
```

### Summary Assessment
- **Security Score**: [1-10] - SQL injection protection, access controls
- **Performance Score**: [1-10] - Query efficiency, index usage
- **Maintainability Score**: [1-10] - Code quality, documentation
- **Schema Quality Score**: [1-10] - Design patterns, normalization

### Top 3 Priority Actions
1. **[Critical Security Fix]**: Address SQL injection vulnerabilities
2. **[Performance Optimization]**: Add missing indexes or optimize queries
3. **[Code Quality]**: Improve naming conventions and documentation

Focus on providing actionable, database-agnostic recommendations while highlighting platform-specific optimizations and best practices.

Review code for AEM Edge Delivery Services (EDS) projects following established coding standards, performance requirements, and best practices.

## Review Priority Levels

### Must Fix (Blocking)
Issues that MUST be resolved before merge:
- Missing preview URLs
- Linting failures
- Security vulnerabilities
- Breaking existing functionality
- Performance regressions (Lighthouse score drop)
- Accessibility violations
- Modifications to `aem.js`

### Should Fix (High Priority)
Issues that SHOULD be resolved:
- `!important` usage without justification
- Unscoped CSS selectors
- Hardcoded values that should be configurable
- Missing error handling
- Console statements left in code
- CSS in JavaScript

### Consider (Suggestions)
Improvements to consider:
- Code organization
- Naming conventions
- Documentation
- Additional test coverage
- Modern API usage opportunities

---

## Common Review Patterns

Based on actual PR reviews, watch for these patterns:

### CSS Issues
- **"No CSS in JS, please"** - Inline styles should use CSS classes
- **"Use proper CSS"** - Avoid style manipulation in JavaScript
- **"Do we need the `!important`?"** - Strong preference against `!important`
- **"Solvable with more CSS specificity"** - Increase specificity instead of `!important`

### JavaScript Issues
- **"Why is it hardcoded here?"** - Configuration should be externalized
- **"No global eslint-disable directives"** - Specific, justified disables only
- **"Clean up console messages"** - Remove debug logging
- **"Use proper feature (e.g., decorateIcons, loadScript)"** - Leverage existing utilities

### Architecture Issues
- **"Use existing patterns"** - Check if similar functionality exists
- **"Consider IntersectionObserver"** - For lazy loading
- **"Extract and align design tokens"** - Use CSS custom properties

### Content Issues
- **"Check the type in content config"** - Validate against expected schema
- **"Is this feature needed?"** - Question value vs complexity

---

## Review Response Templates

### Approval
```markdown

## Approved

Preview URLs verified and changes look good.

### Visual Preview
![Desktop Screenshot](url-or-embedded-image)

<details>
<summary>Mobile View</summary>

![Mobile Screenshot](url-or-embedded-image)

</details>

**Verified:**
- [x] Code quality and linting
- [x] Performance (Lighthouse scores)
- [x] Visual appearance (screenshots captured)
- [x] Responsive behavior
- [x] Accessibility basics

[Any additional notes]
```

### Request Changes
```markdown

## Changes Requested

### Blocking Issues
[List with file:line references]

### Suggestions
[List of recommendations]

Please address the blocking issues before merge.
```

### Comment
```markdown

## Review Notes

[Non-blocking observations and questions]
```

---
