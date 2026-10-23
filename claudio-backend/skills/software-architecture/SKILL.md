---
name: software-architecture
description: 'System design and architecture: choosing boundaries and patterns, designing
  services and data models, reasoning about tradeoffs, and evolving a system without
  rot.'
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-23'
  sources:
  - owner: github
    name: architecture-blueprint-generator
    license: unknown
    url: https://skillmd.com/skills/github/architecture-blueprint-generator
  - owner: alirezarezvani
    name: design-system
    license: unknown
    url: https://skillmd.com/skills/alirezarezvani/design-system
  - owner: cloudai-x
    name: designing-architecture
    license: unknown
    url: https://skillmd.com/skills/cloudai-x/designing-architecture
  - owner: anthropic
    name: canvas-design
    license: Complete terms in LICENSE.txt
    url: https://skillmd.com/skills/anthropic/canvas-design
  - owner: google-labs-code
    name: stitch-extract-design-md
    license: unknown
    url: https://skillmd.com/skills/google-labs-code/stitch-extract-design-md
---

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

## Architecture Decision Workflow

Copy this checklist and track progress:

```
Architecture Design Progress:
- [ ] Step 1: Understand requirements and constraints
- [ ] Step 2: Assess project size and team capabilities
- [ ] Step 3: Select architecture pattern
- [ ] Step 4: Define directory structure
- [ ] Step 5: Document trade-offs and decision
- [ ] Step 6: Validate against decision framework
```

## Pattern Selection Guide

### By Project Size

| Size              | Recommended Pattern               |
| ----------------- | --------------------------------- |
| Small (<10K LOC)  | Simple MVC/Layered                |
| Medium (10K-100K) | Clean Architecture                |
| Large (>100K)     | Modular Monolith or Microservices |

### By Team Size

| Team      | Recommended                  |
| --------- | ---------------------------- |
| 1-3 devs  | Monolith with clear modules  |
| 4-10 devs | Modular Monolith             |
| 10+ devs  | Microservices (if justified) |

## Common Patterns

### 1. Layered Architecture

```
┌─────────────────────────────┐
│       Presentation          │  ← UI, API Controllers
├─────────────────────────────┤
│       Application           │  ← Use Cases, Services
├─────────────────────────────┤
│         Domain              │  ← Business Logic, Entities
├─────────────────────────────┤
│      Infrastructure         │  ← Database, External APIs
└─────────────────────────────┘
```

**Use when**: Simple CRUD apps, small teams, quick prototypes

### 2. Clean Architecture

```
┌─────────────────────────────────────┐
│            Frameworks & Drivers      │
│  ┌─────────────────────────────┐    │
│  │     Interface Adapters       │    │
│  │  ┌─────────────────────┐    │    │
│  │  │   Application       │    │    │
│  │  │  ┌─────────────┐    │    │    │
│  │  │  │   Domain    │    │    │    │
│  │  │  └─────────────┘    │    │    │
│  │  └─────────────────────┘    │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

**Use when**: Complex business logic, long-lived projects, testability is key

### 3. Hexagonal (Ports & Adapters)

```
        ┌──────────┐
        │ HTTP API │
        └────┬─────┘
             │ Port
    ┌────────▼────────┐
    │                 │
    │   Application   │
    │     Core        │
    │                 │
    └────────┬────────┘
             │ Port
        ┌────▼─────┐
        │ Database │
        └──────────┘
```

**Use when**: Need to swap external dependencies, multiple entry points

### 4. Event-Driven Architecture

```
Producer → Event Bus → Consumer
              │
              ├─→ Consumer
              │
              └─→ Consumer
```

**Use when**: Loose coupling needed, async processing, scalability

### 5. CQRS (Command Query Responsibility Segregation)

```
┌─────────────┐      ┌─────────────┐
│  Commands   │      │   Queries   │
│  (Write)    │      │   (Read)    │
└──────┬──────┘      └──────┬──────┘
       │                    │
       ▼                    ▼
  Write Model          Read Model
       │                    │
       └────────┬───────────┘
                ▼
           Event Store
```

**Use when**: Different read/write scaling, complex domains, event sourcing

## Directory Structure Patterns

### Feature-Based (Recommended for medium+)

```
src/
├── features/
│   ├── users/
│   │   ├── api/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── services/
│   │   └── types/
│   └── orders/
│       ├── api/
│       ├── components/
│       └── ...
├── shared/
│   ├── components/
│   ├── hooks/
│   └── utils/
└── app/
    └── ...
```

### Layer-Based (Simple apps)

```
src/
├── controllers/
├── services/
├── models/
├── repositories/
└── utils/
```

## Decision Framework

When making architectural decisions, evaluate against these criteria:

1. **Simplicity** - Start simple, evolve when needed
2. **Team Skills** - Match architecture to team capabilities
3. **Requirements** - Let business needs drive decisions
4. **Scalability** - Consider growth trajectory
5. **Maintainability** - Optimize for change

## Trade-off Analysis Template

Use this template to document architectural decisions:

```markdown

## Decision: [What we're deciding]

### Context

[Why this decision is needed now]

### Options Considered

1. Option A: [Description]
2. Option B: [Description]

### Trade-offs

| Criteria         | Option A | Option B |
| ---------------- | -------- | -------- |
| Complexity       | Low      | High     |
| Scalability      | Medium   | High     |
| Team familiarity | High     | Low      |

### Decision

We chose [Option] because [reasoning].

### Consequences

- [What this enables]
- [What this constrains]
```

## Validation Checklist

After selecting an architecture, validate against:

```
Architecture Validation:
- [ ] Matches project size and complexity
- [ ] Aligns with team skills and experience
- [ ] Supports current requirements
- [ ] Allows for anticipated growth
- [ ] Dependencies flow inward (core has no external deps)
- [ ] Clear boundaries between modules/layers
- [ ] Testing strategy is feasible
- [ ] Trade-offs are documented
```

If validation fails, reconsider the pattern selection or adjust the implementation approach.

These are instructions for creating design philosophies - aesthetic movements that are then EXPRESSED VISUALLY. Output only .md files, .pdf files, and .png files.

Complete this in two steps:
1. Design Philosophy Creation (.md file)
2. Express by creating it on a canvas (.pdf file or .png file)

First, undertake this task:

## DESIGN PHILOSOPHY CREATION

To begin, create a VISUAL PHILOSOPHY (not layouts or templates) that will be interpreted through:
- Form, space, color, composition
- Images, graphics, shapes, patterns
- Minimal text as visual accent

### THE CRITICAL UNDERSTANDING
- What is received: Some subtle input or instructions by the user that should be taken into account, but used as a foundation; it should not constrain creative freedom.
- What is created: A design philosophy/aesthetic movement.
- What happens next: Then, the same version receives the philosophy and EXPRESSES IT VISUALLY - creating artifacts that are 90% visual design, 10% essential text.

Consider this approach:
- Write a manifesto for an art movement
- The next phase involves making the artwork

The philosophy must emphasize: Visual expression. Spatial communication. Artistic interpretation. Minimal words.

### HOW TO GENERATE A VISUAL PHILOSOPHY

**Name the movement** (1-2 words): "Brutalist Joy" / "Chromatic Silence" / "Metabolist Dreams"

**Articulate the philosophy** (4-6 paragraphs - concise but complete):

To capture the VISUAL essence, express how the philosophy manifests through:
- Space and form
- Color and material
- Scale and rhythm
- Composition and balance
- Visual hierarchy

**CRITICAL GUIDELINES:**
- **Avoid redundancy**: Each design aspect should be mentioned once. Avoid repeating points about color theory, spatial relationships, or typographic principles unless adding new depth.
- **Emphasize craftsmanship REPEATEDLY**: The philosophy MUST stress multiple times that the final work should appear as though it took countless hours to create, was labored over with care, and comes from someone at the absolute top of their field. This framing is essential - repeat phrases like "meticulously crafted," "the product of deep expertise," "painstaking attention," "master-level execution."
- **Leave creative space**: Remain specific about the aesthetic direction, but concise enough that the next Claude has room to make interpretive choices also at a extremely high level of craftmanship.

The philosophy must guide the next version to express ideas VISUALLY, not through text. Information lives in design, not paragraphs.

### PHILOSOPHY EXAMPLES

**"Concrete Poetry"**
Philosophy: Communication through monumental form and bold geometry.
Visual expression: Massive color blocks, sculptural typography (huge single words, tiny labels), Brutalist spatial divisions, Polish poster energy meets Le Corbusier. Ideas expressed through visual weight and spatial tension, not explanation. Text as rare, powerful gesture - never paragraphs, only essential words integrated into the visual architecture. Every element placed with the precision of a master craftsman.

**"Chromatic Language"**
Philosophy: Color as the primary information system.
Visual expression: Geometric precision where color zones create meaning. Typography minimal - small sans-serif labels letting chromatic fields communicate. Think Josef Albers' interaction meets data visualization. Information encoded spatially and chromatically. Words only to anchor what color already shows. The result of painstaking chromatic calibration.

**"Analog Meditation"**
Philosophy: Quiet visual contemplation through texture and breathing room.
Visual expression: Paper grain, ink bleeds, vast negative space. Photography and illustration dominate. Typography whispered (small, restrained, serving the visual). Japanese photobook aesthetic. Images breathe across pages. Text appears sparingly - short phrases, never explanatory blocks. Each composition balanced with the care of a meditation practice.

**"Organic Systems"**
Philosophy: Natural clustering and modular growth patterns.
Visual expression: Rounded forms, organic arrangements, color from nature through architecture. Information shown through visual diagrams, spatial relationships, iconography. Text only for key labels floating in space. The composition tells the story through expert spatial orchestration.

**"Geometric Silence"**
Philosophy: Pure order and restraint.
Visual expression: Grid-based precision, bold photography or stark graphics, dramatic negative space. Typography precise but minimal - small essential text, large quiet zones. Swiss formalism meets Brutalist material honesty. Structure communicates, not words. Every alignment the work of countless refinements.

*These are condensed examples. The actual design philosophy should be 4-6 substantial paragraphs.*

### ESSENTIAL PRINCIPLES
- **VISUAL PHILOSOPHY**: Create an aesthetic worldview to be expressed through design
- **MINIMAL TEXT**: Always emphasize that text is sparse, essential-only, integrated as visual element - never lengthy
- **SPATIAL EXPRESSION**: Ideas communicate through space, form, color, composition - not paragraphs
- **ARTISTIC FREEDOM**: The next Claude interprets the philosophy visually - provide creative room
- **PURE DESIGN**: This is about making ART OBJECTS, not documents with decoration
- **EXPERT CRAFTSMANSHIP**: Repeatedly emphasize the final work must look meticulously crafted, labored over with care, the product of countless hours by someone at the top of their field

**The design philosophy should be 4-6 paragraphs long.** Fill it with poetic design philosophy that brings together the core vision. Avoid repeating the same points. Keep the design philosophy generic without mentioning the intention of the art, as if it can be used wherever. Output the design philosophy as a .md file.

---

## DEDUCING THE SUBTLE REFERENCE

**CRITICAL STEP**: Before creating the canvas, identify the subtle conceptual thread from the original request.

**THE ESSENTIAL PRINCIPLE**:
The topic is a **subtle, niche reference embedded within the art itself** - not always literal, always sophisticated. Someone familiar with the subject should feel it intuitively, while others simply experience a masterful abstract composition. The design philosophy provides the aesthetic language. The deduced topic provides the soul - the quiet conceptual DNA woven invisibly into form, color, and composition.

This is **VERY IMPORTANT**: The reference must be refined so it enhances the work's depth without announcing itself. Think like a jazz musician quoting another song - only those who know will catch it, but everyone appreciates the music.

---

## CANVAS CREATION

With both the philosophy and the conceptual framework established, express it on a canvas. Take a moment to gather thoughts and clear the mind. Use the design philosophy created and the instructions below to craft a masterpiece, embodying all aspects of the philosophy with expert craftsmanship.

**IMPORTANT**: For any type of content, even if the user requests something for a movie/game/book, the approach should still be sophisticated. Never lose sight of the idea that this should be art, not something that's cartoony or amateur.

To create museum or magazine quality work, use the design philosophy as the foundation. Create one single page, highly visual, design-forward PDF or PNG output (unless asked for more pages). Generally use repeating patterns and perfect shapes. Treat the abstract philosophical design as if it were a scientific bible, borrowing the visual language of systematic observation—dense accumulation of marks, repeated elements, or layered patterns that build meaning through patient repetition and reward sustained viewing. Add sparse, clinical typography and systematic reference markers that suggest this could be a diagram from an imaginary discipline, treating the invisible subject with the same reverence typically reserved for documenting observable phenomena. Anchor the piece with simple phrase(s) or details positioned subtly, using a limited color palette that feels intentional and cohesive. Embrace the paradox of using analytical visual language to express ideas about human experience: the result should feel like an artifact that proves something ephemeral can be studied, mapped, and understood through careful attention. This is true art. 

**Text as a contextual element**: Text is always minimal and visual-first, but let context guide whether that means whisper-quiet labels or bold typographic gestures. A punk venue poster might have larger, more aggressive type than a minimalist ceramics studio identity. Most of the time, font should be thin. All use of fonts must be design-forward and prioritize visual communication. Regardless of text scale, nothing falls off the page and nothing overlaps. Every element must be contained within the canvas boundaries with proper margins. Check carefully that all text, graphics, and visual elements have breathing room and clear separation. This is non-negotiable for professional execution. **IMPORTANT: Use different fonts if writing text. Search the `./canvas-fonts` directory. Regardless of approach, sophistication is non-negotiable.**

Download and use whatever fonts are needed to make this a reality. Get creative by making the typography actually part of the art itself -- if the art is abstract, bring the font onto the canvas, not typeset digitally.

To push boundaries, follow design instinct/intuition while using the philosophy as a guiding principle. Embrace ultimate design freedom and choice. Push aesthetics and design to the frontier. 

**CRITICAL**: To achieve human-crafted quality (not AI-generated), create work that looks like it took countless hours. Make it appear as though someone at the absolute top of their field labored over every detail with painstaking care. Ensure the composition, spacing, color choices, typography - everything screams expert-level craftsmanship. Double-check that nothing overlaps, formatting is flawless, every detail perfect. Create something that could be shown to people to prove expertise and rank as undeniably impressive.

Output the final result as a single, downloadable .pdf or .png file, alongside the design philosophy used as a .md file.

---

## FINAL STEP

**IMPORTANT**: The user ALREADY said "It isn't perfect enough. It must be pristine, a masterpiece if craftsmanship, as if it were about to be displayed in a museum."

**CRITICAL**: To refine the work, avoid adding more graphics; instead refine what has been created and make it extremely crisp, respecting the design philosophy and the principles of minimalism entirely. Rather than adding a fun filter or refactoring a font, consider how to make the existing composition more cohesive with the art. If the instinct is to call a new function or draw a new shape, STOP and instead ask: "How can I make what's already here more of a piece of art?"

Take a second pass. Go back to the code and refine/polish further to make this a philosophically designed masterpiece.

## MULTI-PAGE OPTION

To create additional pages when requested, create more creative pages along the same lines as the design philosophy but distinctly different as well. Bundle those pages in the same .pdf or many .pngs. Treat the first page as just a single page in a whole coffee table book waiting to be filled. Make the next pages unique twists and memories of the original. Have them almost tell a story in a very tasteful way. Exercise full creative freedom.

Analyze frontend source code to extract a comprehensive design system document
(DESIGN.md) that captures the project's visual language — colors, typography,
spacing, component patterns, and layout principles — directly from the source
files, without needing to build or render the application.

## Why This Exists

The `design-md` skill works from rendered HTML. But often you have a codebase
and want to understand its design system before you can even run the app —
maybe dependencies are missing, the build is broken, or you just want a quick
audit. This skill reads the source files themselves: stylesheets, component
files, theme configs, and Tailwind setups. It's faster and works anywhere.

## When to Use

- User has a frontend codebase and wants to extract or document its design system
- User wants to migrate a project's visual identity into Stitch
- User asks to "audit the styling" or "understand the design language" of a repo
- User wants to create a DESIGN.md from existing source code
- The app can't be built/rendered but the source is available
- User wants to unify or reconcile inconsistent styles across a codebase

## Prerequisites

- Access to the frontend project's source directory
- No build or runtime dependencies needed — this skill reads source files only

---

## Workflow

### Phase 1: Project Discovery

Start by understanding what you're working with. This determines which
extraction patterns to use.

#### 1. Detect the Framework and Stack

Scan the project root for telltale files:

| Signal File | Framework / Tool |
|:---|:---|
| `package.json` with `react` | React / Next.js |
| `package.json` with `vue` | Vue / Nuxt |
| `package.json` with `svelte` | Svelte / SvelteKit |
| `package.json` with `@angular/core` | Angular |
| `tailwind.config.js/ts` | Tailwind CSS |
| `postcss.config.js` | PostCSS pipeline |
| `styled-components` or `@emotion` in deps | CSS-in-JS |
| `.css` / `.scss` / `.less` files only | Plain CSS / SASS |
| `theme.js` / `theme.ts` / `tokens.js` | Design token files |

Read `package.json` first — it reveals the framework, CSS tooling, and any
design-token libraries (e.g., `style-dictionary`, `@chakra-ui/react`,
`@mui/material`, `ant-design`). This context tells you *where* to look for
styling information.

#### 2. Map the Source Tree

Identify the key directories and files you'll analyze:

```
src/
├── components/     ← Component-level styles
├── styles/         ← Global stylesheets
├── theme/          ← Theme definitions, tokens
├── assets/         ← Fonts, images
├── app.css         ← Root styles
└── index.css       ← Entry CSS
```

Also check for:
- `tailwind.config.js` / `tailwind.config.ts` — Custom colors, fonts, spacing
- `globals.css` / `global.css` — CSS custom properties (variables)
- Any `theme.*` or `tokens.*` files
- Component library config (e.g., `chakra-theme.ts`, `vuetify.config.ts`)

#### 3. Read Framework-Specific Guidance

Consult the appropriate reference for extraction patterns:

- **React / Next.js / Tailwind** → [references/react-tailwind.md](references/react-tailwind.md)
- **Vue / Nuxt** → [references/vue.md](references/vue.md)
- **Svelte / SvelteKit** → [references/svelte.md](references/svelte.md)
- **Angular** → [references/angular.md](references/angular.md)
- **Plain CSS / SASS / Less** → [references/plain-css.md](references/plain-css.md)

These references contain framework-specific patterns for locating colors,
typography, spacing, and component styles. Read the one that matches before
proceeding.

---

### Phase 2: Deep Extraction

Work through each design dimension systematically. For each one, gather raw
data from the source files, then synthesize it into descriptive language.

The goal isn't to dump every CSS property — it's to understand the *intent*
behind the styling choices and describe them in human, editorial language that
another designer (or Stitch) can use to recreate the same visual feel.

#### 1. Visual Theme & Atmosphere

Read the broadest styling first to understand the overall mood:

- **Root background**: What's the `body` or root element background? Light
  cream (#f-range) signals airy/clean; dark (#0-#2 range) signals moody/dramatic.
- **Whitespace philosophy**: Are spacing values generous (32px+) or tight?
  Check padding/margin values on root containers, section wrappers, and card components.
- **Density**: Count the components per page/section. Few with space = minimal;
  many packed tight = information-dense.
- **Color temperature**: Are the neutrals warm (creams, tans) or cool (blue-grays, slates)?
- **Overall feel**: Synthesize into 1-2 rich sentences that capture the mood.

Look for these signals in the source:

| Source Location | What It Tells You |
|:---|:---|
| Root `background-color` or Tailwind `bg-*` on layouts | Overall lightness/darkness |
| Spacing scale in Tailwind config or CSS vars | Whitespace philosophy |
| Number of components vs. wrapper padding | Density |
| Custom property naming (`--warm-*` vs `--cool-*`) | Color temperature intent |
| Comments in theme files | Design intent in the developer's own words |

#### 2. Color Palette & Roles

Extract every unique color from the codebase and assign functional roles.
Search across all layers:

**Where to find colors:**

| Layer | What to Search |
|:---|:---|
| CSS custom properties | `--color-*`, `--primary`, `--bg-*` |
| Tailwind config | `theme.extend.colors` |
| Theme/token files | Color objects, palettes |
| Component styles | `background-color`, `color`, `border-color` |
| Inline/scoped styles | `bg-*`, `text-*` classes in templates |
| CSS-in-JS theme objects | `colors`, `palette` keys |

**How to organize:** Group colors by function, not by hue:

1. **Primary Foundation** — Background and surface colors
2. **Accent & Interactive** — CTA buttons, active states, links
3. **Typography & Text Hierarchy** — Primary, secondary, tertiary text
4. **Functional States** — Success, error, warning, info

For each color, create a descriptive name that evokes the color's character
rather than its raw hex value:

- ❌ `#294056` → "Blue"
- ✅ `#294056` → **"Deep Muted Teal-Navy"** — Primary CTA, active navigation

**Deduplication matters.** Codebases often have near-duplicate colors (e.g.,
`#333` and `#2C2C2C`). Consolidate them under one name that best represents
the intended color.

#### 3. Typography Rules

Extract the complete typographic system:

**Font families:**
- Check CSS `font-family`, Tailwind `fontFamily`, Google Fonts links, or
  local `@font-face` declarations.
- Note the **character** of each font: geometric vs humanist, serif vs sans,
  the feeling it evokes.

**Type scale (hierarchy):**
- Find every heading level (H1-H6) and body text, noting:
  - `font-size` (in rem or px)
  - `font-weight` (numeric value + descriptive name)
  - `letter-spacing` (and why — elegance? compactness?)
  - `line-height` (generous for readability? tight for display?)
- Map component usage: Which heading level do product cards use? What about
  hero sections?

**Spacing principles:**
- How does text spacing relate to the overall spacing scale?
- Letter-spacing patterns on headings vs body
- Line-height philosophy (generous/relaxed for body, tighter for display)

#### 4. Component Stylings

Analyze the 4-5 most important UI primitives:

**Buttons:**
- Corner radius (and what it communicates — playful? professional? minimal?)
- Color scheme for primary, secondary, and ghost variants
- Hover/focus/active states and transition timing
- Padding ratios (horizontal vs vertical)

**Cards / Containers:**
- Corner radius (often different from buttons — slightly rounder)
- Shadow strategy: flat, subtle hover shadows, or always elevated?
- Border treatment: hairline borders, colored accents, or none?
- Internal padding (generous or compact?)
- Image treatment within cards (full-bleed, padded, rounded?)

**Navigation:**
- Layout pattern (horizontal bar, vertical sidebar, drawer)
- Typography treatment (uppercase, letter-spacing, weight)
- Active/hover state indicators (underline, color, background)
- Mobile behavior (hamburger, bottom nav, drawer)

**Inputs & Forms:**
- Border style and focus state behavior
- Corner style consistency with buttons
- Padding and touch-target sizing

**Domain-Specific Components:**
- Identify 1-2 components unique to this project (e.g., product cards,
  dashboard widgets, chat bubbles) and describe their styling patterns.

#### 5. Layout Principles

Extract the structural system:

**Grid & Structure:**
- Max content width (from `max-width` on containers)
- Column system (CSS Grid, Flexbox patterns, defined breakpoints)
- Responsive breakpoints (from media queries or Tailwind config)

**Whitespace Strategy:**
- Base spacing unit (8px grid? 4px? custom?)
- Section margins (how much space between major sections)
- Edge padding (page margins at different breakpoints)

**Alignment & Visual Balance:**
- Text alignment patterns (centered heroes, left-aligned body)
- Image-to-text ratios
- Visual weight distribution

**Responsive Behavior:**
- Mobile-first or desktop-first?
- How do grids collapse? Padding scale?
- Touch target sizing

#### 6. Stitch Generation Notes

Synthesize the extraction into actionable prompts for Stitch:

- **Atmosphere language**: Translate the mood into natural descriptors
- **Color references**: List colors by descriptive name + hex
- **Component prompts**: Write 2-3 example prompts that would recreate
  key components in Stitch
- **Iteration guidance**: Tips for refining screens in this design system

---

### Phase 3: Write the DESIGN.md

Assemble everything into the standard DESIGN.md format. Place it at
`.stitch/DESIGN.md` in the project directory (create the `.stitch/` directory
if it doesn't exist).

> [!IMPORTANT]
> You **MUST** include the YAML frontmatter at the top of the file with `name` and `colors` mapping, exactly as shown in the example at [examples/DESIGN.md](examples/DESIGN.md). This structured data is required for other skills to parse the design system.
>
> Failure to include this YAML block with at least the core color tokens is a failure to use this skill correctly.

Use the format from the example at [examples/DESIGN.md](examples/DESIGN.md) as your template. The file must start with the YAML block, followed by the markdown sections:

```markdown
# Design System: [Project Name]
**Project ID:** [If known, otherwise omit]

## 1. Visual Theme & Atmosphere
[Rich 2-paragraph description of mood, philosophy, and key characteristics]

## 2. Color Palette & Roles
### Primary Foundation
### Accent & Interactive
### Typography & Text Hierarchy
### Functional States

## 3. Typography Rules
### Hierarchy & Weights
### Spacing Principles

## 4. Component Stylings
### Buttons
### Cards & [Domain-Specific Containers]
### Navigation
### Inputs & Forms
### [Domain-Specific Components]

## 5. Layout Principles
### Grid & Structure
### Whitespace Strategy
### Alignment & Visual Balance
### Responsive Behavior & Touch

## 6. Design System Notes for Stitch Generation
### Language to Use
### Color References
### Component Prompts
### Incremental Iteration
```

---

### Phase 4: Integration (Optional)

If the user wants to push the design system into Stitch:

1. Hand off to the `manage-design-system` skill for the MCP create/update calls
2. The DESIGN.md you wrote is the input — the manage-design-system skill handles
   the Stitch API integration

If the user just wants the document, you're done after Phase 3.

---

## Quality Checklist

Before delivering the DESIGN.md, verify:

- [ ] Every color has a descriptive name, hex code, and functional role
- [ ] Typography includes font family, character description, and full hierarchy
- [ ] Component styles describe shape, color, states, and transitions
- [ ] Layout includes max-width, grid, breakpoints, and spacing strategy
- [ ] Stitch generation notes use natural language, not CSS syntax
- [ ] The atmosphere section reads like editorial copy, not technical docs
- [ ] Near-duplicate colors are consolidated
- [ ] The document captures the *intent* behind styling, not just raw values

## Tips for Better Extraction

- **Read comments and commit messages.** Developers often document design
  intent in code comments (`/* hero section — breathable */`) and commit
  messages. These are gold for understanding the *why*.
- **Check for design-token libraries.** If the project uses `style-dictionary`,
  `@tokens-studio`, or similar, these files are the most authoritative
  source of design values.
- **Theme files are higher-signal than component styles.** A `theme.ts` that
  defines a palette tells you the intended design system; scattered inline
  styles in components tell you what actually shipped. Both matter, but
  start from the theme.
- **Tailwind config is a design system.** If a project has a customized
  `tailwind.config.js`, that *is* the design system — extract from it first,
  then spot-check components for overrides.
- **CSS custom properties are intentional.** If a developer defined
  `--brand-primary`, they're telling you this is a design token. Respect that.

## Credits

- **github/architecture-blueprint-generator** — license: unknown — https://skillmd.com/skills/github/architecture-blueprint-generator
- **alirezarezvani/design-system** — license: unknown — https://skillmd.com/skills/alirezarezvani/design-system
- **cloudai-x/designing-architecture** — license: unknown — https://skillmd.com/skills/cloudai-x/designing-architecture
- **anthropic/canvas-design** — license: Complete terms in LICENSE.txt — https://skillmd.com/skills/anthropic/canvas-design
- **google-labs-code/stitch-extract-design-md** — license: unknown — https://skillmd.com/skills/google-labs-code/stitch-extract-design-md
