# Design System Documentation: High-Density Tactical UI

## 1. Overview & Creative North Star

### Creative North Star: "The Kinetic Command Deck"
This design system is engineered for the high-stakes environment of AI orchestration and complex data telemetry. It moves away from the "soft" approachable web and toward the aesthetic of a high-end flight control system or a professional IDE. We are building for the "Power User"—an expert who requires maximum information density without cognitive overload.

The system breaks the "template" look through **intentional asymmetry** and **chromatic hierarchy**. Instead of centered, airy layouts, we utilize a "sidebar-heavy" editorial approach where primary controls are anchored to the left and data streams flow in high-contrast "fenced" modules. Every pixel must feel earned; if a component doesn't serve a functional data purpose, it is removed.

---

## 2. Colors & Tonal Logic

The color palette is anchored in "Midnight Slate" to reduce eye strain during long-duration monitoring.

### The Palette
- **Core Background (`surface`):** #0b1326. The foundation of the entire experience.
- **Cyan Precision (`primary_container`):** #22D3EE. Used for active states, data "Go" signals, and primary action paths.
- **Amber Alert (`secondary_container`):** #EE9800. Reserved strictly for warnings, anomalies, or high-priority AI insights.
- **Crisp Grays (`on_surface_variant`):** #BBC9CD. Used for secondary labels and non-essential telemetry.

### The "No-Line" Rule
To achieve a premium feel, **prohibit 1px solid borders for sectioning.** Physical boundaries must be defined solely through background color shifts.
- A card should not have a border; it should sit as a `surface_container_high` block on a `surface` background.
- Use the **Spacing Scale** (e.g., `spacing.px` or `spacing.0.5`) to create "micro-gaps" between containers, allowing the background to act as a natural separator.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers.
- **Base:** `surface_dim` (#0b1326).
- **Secondary Panels:** `surface_container_low`.
- **Active Workspaces:** `surface_container_high`.
- **Floating Overlays:** `surface_bright`.

### The "Glass & Gradient" Rule
While we avoid "soft" noise, we use **Tactical Glass** for floating command palettes. Use a semi-transparent `surface_container_highest` with a `backdrop-blur` of 12px. For primary CTAs, apply a subtle linear gradient from `primary` (#8aebff) to `primary_container` (#22d3ee) at a 135-degree angle to provide a "lit from within" professional polish.

---

## 3. Typography

The typography strategy separates "Human Interface" from "Machine Logic."

### The "Logic" Font: JetBrains Mono
Used exclusively for:
- Data values, code snippets, status tags, and timestamps.
- All `label-sm` and `label-md` elements associated with metrics.
- It conveys precision and a "pro-tool" aesthetic.

### The "Interface" Font: Inter
Used for:
- Navigation, headlines, and descriptive body text.
- **Display Scale:** Use `display-md` (2.75rem) with a negative letter-spacing (-0.02em) for hero metrics to create an authoritative, editorial look.
- **Body Scale:** Keep `body-md` (0.875rem) as the workhorse for high-density descriptions.

---

## 4. Elevation & Depth

In a tactical system, traditional drop shadows feel "muddy." We replace them with **Tonal Layering**.

- **The Layering Principle:** Place a `surface_container_highest` element over a `surface_container_low` base. The contrast in hex values creates a "soft lift" that is cleaner than a shadow.
- **Ghost Borders:** If accessibility requires a stroke (e.g., in a high-density data grid), use a "Ghost Border." Apply the `outline_variant` token at **15% opacity**. This defines the edge without cluttering the visual field.
- **Sharp Corners:** All containers must adhere to `roundedness.none` (0px). This reinforces the "flight instrument" feel and allows for seamless tiling of data modules.

---

## 5. Components

### Buttons
- **Primary:** Sharp-edged (0px), background `primary_container`, text `on_primary_container`. No border.
- **Tertiary:** `on_surface` text with no background. On hover, shift background to `surface_container_high`.
- **Tactical Toggle:** Use `primary_fixed_dim` for "On" states and `surface_container_highest` for "Off" states.

### Data Chips (Tags)
- Use **JetBrains Mono** for the text.
- Height should be fixed at `spacing.5` (1.1rem) for extreme density.
- Backgrounds should be desaturated versions of the intent (e.g., `surface_variant` for neutral tags).

### Input Fields
- Avoid four-sided boxes. Use a bottom-border only (the "Ghost Border" rule) or a subtle `surface_container_lowest` background. 
- Use `label-sm` (Inter) for the floating label and `body-md` (JetBrains Mono) for the input text to emphasize the "data" being entered.

### High-Density Cards & Lists
- **Forbid dividers.** Use `spacing.3` (0.6rem) vertical whitespace to separate items.
- In lists, use a 2px vertical accent bar of `primary` on the left side of the "active" or "hovered" item to indicate focus.

---

## 6. Do's and Don'ts

### Do:
- **Use Mono for Numbers:** Always use JetBrains Mono for numerals to ensure tabular alignment (monospacing).
- **Embrace Density:** It is okay to have small font sizes (`label-sm`) if the information is secondary. Trust the expert user.
- **Asymmetric Balance:** Align your main telemetry to a 12-column grid, but feel free to let a "terminal" style log occupy a non-standard 3.5-column width.

### Don't:
- **No Border Radius:** Never use `rounded-md` or `rounded-lg`. This system is 0px throughout.
- **No Standard Shadows:** Do not use `box-shadow: 0 4px 6px...`. Use background color steps for elevation.
- **No Vibrant Gradients for Backgrounds:** Keep the background flat `surface` (#0b1326). Gradients are for interactive elements only.
- **No "Vague" Icons:** Every icon must be accompanied by a label or have a tooltip with a 0ms delay. In a tactical UI, ambiguity is a failure.

---

## 7. Signature UI Patterns

### The "Telemetry Strip"
A thin horizontal or vertical bar (using `surface_container_highest`) that houses real-time stream data (e.g., CPU usage, AI confidence scores) in `label-sm` JetBrains Mono. This should be persistent across all dashboard views.

### The "Focus Fence"
When a user selects a data point, wrap the module in a `primary` (Cyan) Ghost Border (20% opacity). This "fencing" indicates the system is currently processing or observing that specific data cluster.