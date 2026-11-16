# Design-system brand onboarding (palette, typography, WCAG)

Interactive brand/design-system onboarding that produces the 12 CSS custom-property palette every HTML converter reads. Moved out of `software-architecture` (issue #53): it is visual/brand guidance, not system design.

> Source skill: `design-system` (alirezarezvani) — moved from the software-architecture skill; preserve attribution.

## Configuration Variables
${PROJECT_TYPE="Auto-detect|.NET|Java|React|Angular|Python|Node.js|Flutter|Other"} <!-- Primary technology -->
${ARCHITECTURE_PATTERN="Auto-detect|Clean Architecture|Microservices|Layered|MVVM|MVC|Hexagonal|Event-Driven|Serverless|Monolithic|Other"} <!-- Primary architectural pattern -->
${DIAGRAM_TYPE="C4|UML|Flow|Component|None"} <!-- Architecture diagram type -->
${DETAIL_LEVEL="High-level|Detailed|Comprehensive|Implementation-Ready"} <!-- Level of detail to include -->
${INCLUDES_CODE_EXAMPLES=true|false} <!-- Include sample code to illustrate patterns -->
${INCLUDES_IMPLEMENTATION_PATTERNS=true|false} <!-- Include detailed implementation patterns -->
${INCLUDES_DECISION_RECORDS=true|false} <!-- Include architectural decision records -->
${FOCUS_ON_EXTENSIBILITY=true|false} <!-- Emphasize extension points and patterns -->

## Generated Prompt

"Create a comprehensive 'Project_Architecture_Blueprint.md' document that thoroughly analyzes the architectural patterns in the codebase to serve as a definitive reference for maintaining architectural consistency. Use the following approach:

### 1. Architecture Detection and Analysis
- ${PROJECT_TYPE == "Auto-detect" ? "Analyze the project structure to identify all technology stacks and frameworks in use by examining:
  - Project and configuration files
  - Package dependencies and import statements
  - Framework-specific patterns and conventions
  - Build and deployment configurations" : "Focus on ${PROJECT_TYPE} specific patterns and practices"}
  
- ${ARCHITECTURE_PATTERN == "Auto-detect" ? "Determine the architectural pattern(s) by analyzing:
  - Folder organization and namespacing
  - Dependency flow and component boundaries
  - Interface segregation and abstraction patterns
  - Communication mechanisms between components" : "Document how the ${ARCHITECTURE_PATTERN} architecture is implemented"}

### 2. Architectural Overview
- Provide a clear, concise explanation of the overall architectural approach
- Document the guiding principles evident in the architectural choices
- Identify architectural boundaries and how they're enforced
- Note any hybrid architectural patterns or adaptations of standard patterns

### 3. Architecture Visualization
${DIAGRAM_TYPE != "None" ? `Create ${DIAGRAM_TYPE} diagrams at multiple levels of abstraction:
- High-level architectural overview showing major subsystems
- Component interaction diagrams showing relationships and dependencies
- Data flow diagrams showing how information moves through the system
- Ensure diagrams accurately reflect the actual implementation, not theoretical patterns` : "Describe the component relationships based on actual code dependencies, providing clear textual explanations of:
- Subsystem organization and boundaries
- Dependency directions and component interactions
- Data flow and process sequences"}

### 4. Core Architectural Components
For each architectural component discovered in the codebase:

- **Purpose and Responsibility**:
  - Primary function within the architecture
  - Business domains or technical concerns addressed
  - Boundaries and scope limitations

- **Internal Structure**:
  - Organization of classes/modules within the component
  - Key abstractions and their implementations
  - Design patterns utilized

- **Interaction Patterns**:
  - How the component communicates with others
  - Interfaces exposed and consumed
  - Dependency injection patterns
  - Event publishing/subscription mechanisms

- **Evolution Patterns**:
  - How the component can be extended
  - Variation points and plugin mechanisms
  - Configuration and customization approaches

### 5. Architectural Layers and Dependencies
- Map the layer structure as implemented in the codebase
- Document the dependency rules between layers
- Identify abstraction mechanisms that enable layer separation
- Note any circular dependencies or layer violations
- Document dependency injection patterns used to maintain separation

### 6. Data Architecture
- Document domain model structure and organization
- Map entity relationships and aggregation patterns
- Identify data access patterns (repositories, data mappers, etc.)
- Document data transformation and mapping approaches
- Note caching strategies and implementations
- Document data validation patterns

### 7. Cross-Cutting Concerns Implementation
Document implementation patterns for cross-cutting concerns:

- **Authentication & Authorization**:
  - Security model implementation
  - Permission enforcement patterns
  - Identity management approach
  - Security boundary patterns

- **Error Handling & Resilience**:
  - Exception handling patterns
  - Retry and circuit breaker implementations
  - Fallback and graceful degradation strategies
  - Error reporting and monitoring approaches

- **Logging & Monitoring**:
  - Instrumentation patterns
  - Observability implementation
  - Diagnostic information flow
  - Performance monitoring approach

- **Validation**:
  - Input validation strategies
  - Business rule validation implementation
  - Validation responsibility distribution
  - Error reporting patterns

- **Configuration Management**:
  - Configuration source patterns
  - Environment-specific configuration strategies
  - Secret management approach
  - Feature flag implementation

### 8. Service Communication Patterns
- Document service boundary definitions
- Identify communication protocols and formats
- Map synchronous vs. asynchronous communication patterns
- Document API versioning strategies
- Identify service discovery mechanisms
- Note resilience patterns in service communication

### 9. Technology-Specific Architectural Patterns
${PROJECT_TYPE == "Auto-detect" ? "For each detected technology stack, document specific architectural patterns:" : `Document ${PROJECT_TYPE}-specific architectural patterns:`}

${(PROJECT_TYPE == ".NET" || PROJECT_TYPE == "Auto-detect") ? 
"#### .NET Architectural Patterns (if detected)
- Host and application model implementation
- Middleware pipeline organization
- Framework service integration patterns
- ORM and data access approaches
- API implementation patterns (controllers, minimal APIs, etc.)
- Dependency injection container configuration" : ""}

${(PROJECT_TYPE == "Java" || PROJECT_TYPE == "Auto-detect") ? 
"#### Java Architectural Patterns (if detected)
- Application container and bootstrap process
- Dependency injection framework usage (Spring, CDI, etc.)
- AOP implementation patterns
- Transaction boundary management
- ORM configuration and usage patterns
- Service implementation patterns" : ""}

${(PROJECT_TYPE == "React" || PROJECT_TYPE == "Auto-detect") ? 
"#### React Architectural Patterns (if detected)
- Component composition and reuse strategies
- State management architecture
- Side effect handling patterns
- Routing and navigation approach
- Data fetching and caching patterns
- Rendering optimization strategies" : ""}

${(PROJECT_TYPE == "Angular" || PROJECT_TYPE == "Auto-detect") ? 
"#### Angular Architectural Patterns (if detected)
- Module organization strategy
- Component hierarchy design
- Service and dependency injection patterns
- State management approach
- Reactive programming patterns
- Route guard implementation" : ""}

${(PROJECT_TYPE == "Python" || PROJECT_TYPE == "Auto-detect") ? 
"#### Python Architectural Patterns (if detected)
- Module organization approach
- Dependency management strategy
- OOP vs. functional implementation patterns
- Framework integration patterns
- Asynchronous programming approach" : ""}

### 10. Implementation Patterns
${INCLUDES_IMPLEMENTATION_PATTERNS ? 
"Document concrete implementation patterns for key architectural components:

- **Interface Design Patterns**:
  - Interface segregation approaches
  - Abstraction level decisions
  - Generic vs. specific interface patterns
  - Default implementation patterns

- **Service Implementation Patterns**:
  - Service lifetime management
  - Service composition patterns
  - Operation implementation templates
  - Error handling within services

- **Repository Implementation Patterns**:
  - Query pattern implementations
  - Transaction management
  - Concurrency handling
  - Bulk operation patterns

- **Controller/API Implementation Patterns**:
  - Request handling patterns
  - Response formatting approaches
  - Parameter validation
  - API versioning implementation

- **Domain Model Implementation**:
  - Entity implementation patterns
  - Value object patterns
  - Domain event implementation
  - Business rule enforcement" : "Mention that detailed implementation patterns vary across the codebase."}

### 11. Testing Architecture
- Document testing strategies aligned with the architecture
- Identify test boundary patterns (unit, integration, system)
- Map test doubles and mocking approaches
- Document test data strategies
- Note testing tools and frameworks integration

### 12. Deployment Architecture
- Document deployment topology derived from configuration
- Identify environment-specific architectural adaptations
- Map runtime dependency resolution patterns
- Document configuration management across environments
- Identify containerization and orchestration approaches
- Note cloud service integration patterns

### 13. Extension and Evolution Patterns
${FOCUS_ON_EXTENSIBILITY ? 
"Provide detailed guidance for extending the architecture:

- **Feature Addition Patterns**:
  - How to add new features while preserving architectural integrity
  - Where to place new components by type
  - Dependency introduction guidelines
  - Configuration extension patterns

- **Modification Patterns**:
  - How to safely modify existing components
  - Strategies for maintaining backward compatibility
  - Deprecation patterns
  - Migration approaches

- **Integration Patterns**:
  - How to integrate new external systems
  - Adapter implementation patterns
  - Anti-corruption layer patterns
  - Service facade implementation" : "Document key extension points in the architecture."}

${INCLUDES_CODE_EXAMPLES ? 
"### 14. Architectural Pattern Examples
Extract representative code examples that illustrate key architectural patterns:

- **Layer Separation Examples**:
  - Interface definition and implementation separation
  - Cross-layer communication patterns
  - Dependency injection examples

- **Component Communication Examples**:
  - Service invocation patterns
  - Event publication and handling
  - Message passing implementation

- **Extension Point Examples**:
  - Plugin registration and discovery
  - Extension interface implementations
  - Configuration-driven extension patterns

Include enough context with each example to show the pattern clearly, but keep examples concise and focused on architectural concepts." : ""}

${INCLUDES_DECISION_RECORDS ? 
"### 15. Architectural Decision Records
Document key architectural decisions evident in the codebase:

- **Architectural Style Decisions**:
  - Why the current architectural pattern was chosen
  - Alternatives considered (based on code evolution)
  - Constraints that influenced the decision

- **Technology Selection Decisions**:
  - Key technology choices and their architectural impact
  - Framework selection rationales
  - Custom vs. off-the-shelf component decisions

- **Implementation Approach Decisions**:
  - Specific implementation patterns chosen
  - Standard pattern adaptations
  - Performance vs. maintainability tradeoffs

For each decision, note:
- Context that made the decision necessary
- Factors considered in making the decision
- Resulting consequences (positive and negative)
- Future flexibility or limitations introduced" : ""}

### ${INCLUDES_DECISION_RECORDS ? "16" : INCLUDES_CODE_EXAMPLES ? "15" : "14"}. Architecture Governance
- Document how architectural consistency is maintained
- Identify automated checks for architectural compliance
- Note architectural review processes evident in the codebase
- Document architectural documentation practices

### ${INCLUDES_DECISION_RECORDS ? "17" : INCLUDES_CODE_EXAMPLES ? "16" : "15"}. Blueprint for New Development
Create a clear architectural guide for implementing new features:

- **Development Workflow**:
  - Starting points for different feature types
  - Component creation sequence
  - Integration steps with existing architecture
  - Testing approach by architectural layer

- **Implementation Templates**:
  - Base class/interface templates for key architectural components
  - Standard file organization for new components
  - Dependency declaration patterns
  - Documentation requirements

- **Common Pitfalls**:
  - Architecture violations to avoid
  - Common architectural mistakes
  - Performance considerations
  - Testing blind spots

Include information about when this blueprint was generated and recommendations for keeping it updated as the architecture evolves."

The design-system skill is the **shared brand owner** for the markdown-html plugin. Run its onboarding once. Every converter (`md-document`, `md-review`, `md-slides`) reads the resulting config via `config_loader.py` and applies the same 12 CSS custom properties to its output. Without this, conversions render with placeholder defaults — technically functional but unbranded.

This skill ships exactly three Python tools:

1. **`onboard.py`** — interactive (or `--defaults` / `--set` / `--show` / `--reset`) wizard.
2. **`config_loader.py`** — importable customization loader with project > global > defaults precedence and `MARKDOWN_HTML_NO_CONFIG=1` bypass.
3. **`brand_palette_validator.py`** — WCAG-AA contrast checker + HSL palette deriver.

All three are stdlib-only and contain no LLM calls (deterministic per Path-B discipline).

## When to invoke

| Symptom | Action |
|---|---|
| User says "convert this markdown to HTML" for the first time in this workspace | Run `python3 markdown-html/skills/design-system/scripts/onboard.py` |
| `~/.config/markdown-html/design-system.json` doesn't exist OR `setup_completed_at` is null | Refuse conversion, surface onboarding |
| User wants per-repo brand override | `python3 .../onboard.py --scope project` |
| User wants to change a single field non-interactively | `python3 .../onboard.py --set brand.primary=#FF6B35` |
| User wants to reset and re-onboard | `python3 .../onboard.py --reset` then re-run |
| User wants zero-touch defaults (CI, ephemeral session) | `python3 .../onboard.py --defaults` |
| Headless / containerized run that should ignore saved config | `MARKDOWN_HTML_NO_CONFIG=1 ...` |

## Onboarding question set (10 questions)

| # | Key | Choices / Validator | Default |
|---|---|---|---|
| 1 | `default_output_dir` | path; `os.access(parent, os.W_OK)` | `./markdown-html-out/` |
| 2 | `brand.primary` | HEX `^#?[0-9a-fA-F]{6}$` | `#0A1628` |
| 3 | `brand.accent` | HEX or blank (auto-derive) | derive from primary |
| 4 | `typography.heading_font` | Google Font name (12 safe defaults) | `Inter` |
| 5 | `typography.body_font` | Google Font name | `Inter` |
| 6 | `design_style` | `editorial / technical / minimal / playful` | `technical` |
| 7 | `code_theme` | `light / dark / auto` | `auto` |
| 8 | `toc.behavior` | `sticky-sidebar / collapsible-top / inline / none` | `sticky-sidebar` |
| 9 | `company_name` | string (may be empty) | `""` |
| 10 | `logo_url` | URL or empty (base64-embedded at render) | `""` |

## Hard rules

1. **WCAG AA body-text contrast must pass.** `brand_palette_validator.validate()` runs after every change. Body text on bg must reach 4.5:1; link on bg must reach 4.5:1. If either fails, `onboard.py` refuses to save (exit code 4) and tells the user to pick a darker primary, blank `brand.bg`/`brand.text` to let derivation pick a safe pair, or override `brand.text` directly. Canon: WCAG 2.2 §1.4.3.
2. **Output directory must be writable.** `onboard.py` walks up the path to find an existing ancestor and checks `os.W_OK`. Empty or unwritable path → exit code 3. The orchestrator's `output_path_resolver.py` honors the same rule per-conversion.
3. **Customization must change behavior, not sit as decoration.** Every consumer (md-document, md-review, md-slides) must read the config and render differently when the user changes `design_style`, `brand.primary`, `code_theme`, or `toc.behavior`. Decorative-only fields fail the design discipline.
4. **Precedence is fixed.** Project > global > defaults. The deep-merge preserves nested keys (e.g. you can override `brand.primary` in a project config without losing `typography.heading_font` from global).
5. **Bypass env exists for a reason.** `MARKDOWN_HTML_NO_CONFIG=1` is for headless CI, ephemeral test containers, and the autoresearch-style evaluator loops. Never set it silently for an interactive user.

## Derived 12-token palette

Once the user's brand is captured, `brand_palette_validator.derive_palette()` produces 12 CSS custom properties stored under `derived_palette` in the same config file. Every converter inlines these into its `<style>` block.

| Token | Purpose | Derivation |
|---|---|---|
| `--md-bg` | Document background | Primary if dark, near-neutral if vibrant |
| `--md-surface` | Card / callout / blockquote background | Bg ± 4-6% luminance |
| `--md-border` | Hairline dividers, table borders | Bg ± 8-12% luminance |
| `--md-text` | Body text | Off-white on dark bg, near-black on light bg |
| `--md-text-muted` | Captions, metadata, footers | `rgba(text, 0.68)` |
| `--md-accent` | Primary CTA, callout headers, link emphasis | Primary if vibrant, hue-shifted lighter if dark |
| `--md-accent-soft` | Accent backgrounds, hover states | `rgba(accent, 0.14)` |
| `--md-code-bg` | Inline code, fenced block bg | Bg ± 4-5% luminance |
| `--md-link` | Hyperlinks | Iteratively walked to reach 4.5:1 contrast on bg |
| `--md-link-hover` | Hover state | Link ± 6-8% luminance |
| `--md-success` | OK / approved / passed | Green anchored, luminance-matched |
| `--md-warn` | Caution / nit / TODO | Amber anchored, luminance-matched |

## Forcing-question library (Matt Pocock grill-with-docs pattern)

One question per turn, recommended answer, canon citation.

1. **What's your brand primary color?** Recommended: a HEX you already use in your product or docs — not a stock blue. Canon: Aarron Walter, *Designing for Emotion* (color carries brand affect).
2. **Should accent be derived or set?** Recommended: derive on first run (hue-shift + lighten produces a coherent companion); set explicitly only if your brand kit specifies one. Canon: Adobe Spectrum, *Color Foundations*.
3. **Editorial, technical, minimal, or playful?** Recommended: `technical` for engineering specs/reports, `editorial` for long-read narratives, `minimal` for sparse reference docs, `playful` for marketing/landing content. Canon: Ellen Lupton, *Thinking with Type* (style serves the rhetorical purpose).
4. **Sticky-sidebar TOC, or inline?** Recommended: `sticky-sidebar` for documents over 800 words, `inline` for short reads. Canon: Nielsen-Norman, *Table of Contents Best Practices* (2023).
5. **Save to global or per-project?** Recommended: global by default (consistent across your work); use `--scope project` only when this repo has a different brand. Canon: research-ops onboarding pattern, `research-ops/CLAUDE.md` §8.

## Customization in use (worked example)

```bash
# First-run onboarding (interactive, walks all 10 questions)
python3 markdown-html/skills/design-system/scripts/onboard.py

# Zero-touch defaults for CI / first-test
python3 .../onboard.py --defaults

# Change just the primary color and design style
python3 .../onboard.py --set brand.primary=#FF6B35 --set design_style=editorial

# Per-repo override
python3 .../onboard.py --scope project --set design_style=minimal

# Reset and re-onboard
python3 .../onboard.py --reset
python3 .../onboard.py

# Inspect the effective config (project > global > defaults)
python3 .../config_loader.py --show
python3 .../config_loader.py --status

# Bypass saved config (returns DEFAULTS only)
MARKDOWN_HTML_NO_CONFIG=1 python3 .../config_loader.py --show

# Spot-check WCAG contrast before committing to a brand
python3 .../brand_palette_validator.py --primary "#FF6B35" --accent "#00D4AA"
```

## Assumptions

1. User has at least one brand HEX they want consistent across their HTML conversions.
2. User accepts a 1-2 minute one-time setup.
3. User is OK with Google Fonts as the typography source (CDN, no local font hosting).
4. WCAG 2.2 AA is the accessibility floor (4.5:1 body, 3:1 large/UI). AAA (7:1) is out of scope.

## Non-goals

- Not a full design-token system (Style Dictionary, Theo). Twelve tokens, not a hundred.
- Not a custom-font hosting solution. Google Fonts only.
- Not a dark/light mode switcher in the converters. `code_theme: auto` handles the prefers-color-scheme case for syntax highlighting; layout palette is single-mode per onboarding.
- Not an accessibility audit suite (use axe-core / pa11y for that). We enforce contrast only.
- Does not transform existing CSS — the derived palette is injected into freshly generated HTML.

## Distinct from

- **`marketing/landing/skills/landing/scripts/brand_palette_validator.py`** — that script's `derive_palette()` produces 8 tokens shaped for hero-page rendering (`--navy`, `--teal`, `--card-bg`, `--card-border`). This script produces 12 tokens shaped for document rendering (sticky surface, hairline border, code bg, link, link-hover, success, warn). Same WCAG + HSL math, different token taxonomy.
- **`research-ops/skills/clinical-research/scripts/onboard.py`** — same pattern (interactive + `--defaults`/`--set`/`--show`/`--reset`/`--scope`), different question set (clinical alpha/power/dropout vs. brand palette/typography/layout).

## Output artifact

`~/.config/markdown-html/design-system.json` (global) or `./.markdown-html/design-system.json` (project). JSON schema lives at `assets/design_system_schema.json`.

## Anti-patterns (do not)

- ❌ Skip onboarding and run a converter with placeholder defaults — output looks unbranded.
- ❌ Pick a vibrant brand primary as `brand.bg` directly (low text contrast). Use it as accent instead.
- ❌ Set `MARKDOWN_HTML_NO_CONFIG=1` silently for an interactive user — they'll wonder why their tokens disappeared.
- ❌ Encode brand semantics in `derived_palette` outside the 12-token taxonomy. Add a new token only with a deliberate name + purpose + derivation rule.

## References

- WCAG 2.2 — §1.4.3 (contrast), §1.4.4 (resize), §1.4.11 (non-text contrast)
- Aarron Walter — *Designing for Emotion* (A Book Apart)
- Ellen Lupton — *Thinking with Type*
- Adobe Spectrum — *Color Foundations*
- Nielsen-Norman — *Table of Contents Best Practices* (2023)
- research-ops onboarding pattern: `research-ops/CLAUDE.md` §8
- Brand palette math source: `marketing/landing/skills/landing/scripts/brand_palette_validator.py`

### When to Load

- **Trigger**: System design, module structure, new project scaffolding, choosing architecture patterns
- **Skip**: Simple bug fixes or minor code changes that don't affect architecture
