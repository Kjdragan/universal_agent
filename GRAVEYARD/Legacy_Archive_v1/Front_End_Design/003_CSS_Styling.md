# 003 - CSS Styling

This document explains the CSS that makes the UI look futuristic and how the styling system works.

---

## CSS Variables (Design Tokens)

At the top of the `<style>` section, we define **CSS variables** (also called "custom properties"). These are reusable values:

```css
:root {
    --bg-deep: #0a0b14;           /* Very dark blue-black */
    --bg-panel: rgba(15, 18, 30, 0.85);  /* Translucent dark */
    --accent-cyan: #00f5d4;       /* Primary accent (bright teal) */
    --accent-amber: #ffb86c;      /* Secondary (orange) */
    --accent-magenta: #ff79c6;    /* Tertiary (pink) */
    --accent-green: #50fa7b;      /* Success state */
    --text-primary: #f8f8f2;      /* Main text (off-white) */
    --text-secondary: #a8b2d1;    /* Dimmer text */
    --text-muted: #6272a4;        /* Very dim text */
    --border-glow: rgba(0, 245, 212, 0.4);
    --glass-blur: blur(20px);     /* Frosted glass effect */
}
```

**Why variables?** To use `var(--accent-cyan)` everywhere instead of repeating `#00f5d4`. Change it once, changes everywhere.

---

## The Grid Layout

The main container uses CSS Grid:

```css
.container {
    display: grid;
    grid-template-columns: 320px 1fr 600px;  /* sidebar | flexible | neural */
    grid-template-rows: auto 1fr auto;        /* header | content | input */
    gap: 16px;
    height: 100vh;   /* Full viewport height */
    padding: 16px;
}
```

This creates a 3-column, 3-row grid. Elements are placed using:

```css
.sidebar      { grid-column: 1; grid-row: 2 / 4; }  /* Left, spans rows 2-3 */
.chat-panel   { grid-column: 2; grid-row: 2; }      /* Center, row 2 */
.input-panel  { grid-column: 2; grid-row: 3; }      /* Center, row 3 */
.neural-panel { grid-column: 3; grid-row: 2 / 4; }  /* Right, spans rows 2-3 */
```

---

## Glassmorphism Effect

The "frosted glass" look on panels:

```css
.panel {
    background: var(--bg-panel);        /* Semi-transparent dark */
    backdrop-filter: var(--glass-blur); /* Blur what's behind */
    border: 1px solid rgba(0, 245, 212, 0.2);
    border-radius: 16px;
}

.panel:hover {
    border-color: var(--border-glow);
    box-shadow: 0 0 30px rgba(0, 245, 212, 0.1);  /* Glow on hover */
}
```

**`backdrop-filter: blur(20px)`** blurs the background behind the element, creating the glass effect.

---

## Animations

### The Pulsing Status Indicator

```css
.status-indicator {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent-cyan);
    animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { 
        opacity: 1; 
        box-shadow: 0 0 10px var(--accent-cyan); 
    }
    50% { 
        opacity: 0.5; 
        box-shadow: 0 0 5px var(--accent-cyan); 
    }
}
```

**`animation: pulse 2s ease-in-out infinite`** means:
- Run the `pulse` keyframes
- Over 2 seconds
- With smooth easing
- Forever

### The "Processing" State

When processing, JavaScript adds `.processing` class:

```css
.status-indicator.processing {
    background: var(--accent-amber);  /* Orange instead of cyan */
    animation: pulseAmber 1s ease-in-out infinite;  /* Faster pulse */
}
```

---

## Message Styling

Agent messages vs User messages:

```css
.message.agent .message-bubble {
    background: rgba(0, 245, 212, 0.08);  /* Subtle cyan tint */
    border: 1px solid rgba(0, 245, 212, 0.2);
    border-top-left-radius: 4px;  /* "Speech bubble" corner */
}

.message.user .message-bubble {
    background: rgba(255, 121, 198, 0.1);  /* Subtle magenta tint */
    border: 1px solid rgba(255, 121, 198, 0.3);
    border-top-right-radius: 4px;
}

.message.user {
    flex-direction: row-reverse;  /* Avatar on right side */
}
```

---

## The Toggle Buttons

```css
.view-toggle-btn {
    padding: 6px 14px;
    border-radius: 8px;
    border: 1px solid rgba(0, 245, 212, 0.3);
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    transition: all 0.2s ease;
}

.view-toggle-btn.active {
    background: rgba(0, 245, 212, 0.15);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
}
```

JavaScript toggles the `.active` class when you click.

---

## The Output Panel

```css
.output-panel {
    display: none;  /* Hidden by default */
    height: 100%;
}

.output-panel.active {
    display: flex;  /* Shown when .active is added */
}

.output-frame {
    flex: 1;
    width: 100%;
    border: none;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.98);  /* White for HTML content */
}
```

---

## Scrollbar Styling

Custom scrollbars for the dark theme:

```css
.panel-content::-webkit-scrollbar {
    width: 6px;
}

.panel-content::-webkit-scrollbar-thumb {
    background: rgba(0, 245, 212, 0.3);
    border-radius: 3px;
}
```

---

## Responsive Design

For smaller screens (under 1100px wide):

```css
@media (max-width: 1100px) {
    .container {
        grid-template-columns: 1fr;  /* Single column */
        grid-template-rows: auto 1fr auto auto;
    }
    
    .sidebar { grid-row: 4; }
    .neural-panel { grid-row: 3; }
}
```

---

## Next: 004 - JavaScript WebSocket Client
