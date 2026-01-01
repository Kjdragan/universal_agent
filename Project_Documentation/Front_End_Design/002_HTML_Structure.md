# 002 - HTML Structure

This document explains the HTML structure of `universal_agent_ui.html` - what each section does and why.

---

## Document Structure

The HTML file is a **single-page application** (SPA). Everything is in one file:
- HTML structure at the top
- CSS styles in `<style>` tags
- JavaScript at the bottom in `<script>` tags

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <!-- Fonts and CSS styles -->
</head>
<body>
    <!-- Background effects -->
    <!-- Main container with panels -->
    <script>
        <!-- JavaScript logic -->
    </script>
</body>
</html>
```

---

## The Main Container Layout

The UI uses **CSS Grid** to create a 3-column layout:

```
┌─────────────────────────────────────────────────────────────┐
│                      HEADER (full width)                    │
│  Logo | Status | Session Selector                           │
├──────────────┬──────────────────────┬───────────────────────┤
│              │                      │                       │
│   SIDEBAR    │     CHAT PANEL       │    NEURAL PANEL       │
│   (320px)    │     (flexible)       │    (600px)            │
│              │                      │                       │
│  Workspace   │  Chat | Output       │  Metrics              │
│  Files       │  (toggleable)        │  Activity Stream      │
│              │                      │                       │
│              ├──────────────────────┤                       │
│              │    INPUT PANEL       │                       │
│              │  Textarea + Send     │                       │
└──────────────┴──────────────────────┴───────────────────────┘
```

---

## Key HTML Elements (with IDs)

These are the elements that JavaScript interacts with:

| Element ID | What It Is | JavaScript Uses It For |
|------------|-----------|------------------------|
| `statusIndicator` | The pulsing dot | Changes color when processing |
| `statusText` | "Ready", "Processing" | Shows current status |
| `messages` | Container for chat bubbles | Appends new messages |
| `contextItems` | Tool outputs column | Shows tool results |
| `chatInput` | The textarea | Gets user input |
| `chatView` | Chat columns container | Hide/show for toggle |
| `outputPanel` | Output iframe container | Show HTML reports |
| `outputFrame` | The `<iframe>` | Renders HTML content |

---

## The Chat Panel Structure

The chat panel has **two views** that toggle:

### 1. Chat View (default)
```html
<div class="chat-view" id="chatView">
    <div class="chat-columns">
        <!-- Left: Conversation -->
        <div class="chat-column">
            <div id="messages">
                <!-- Messages go here dynamically -->
            </div>
        </div>
        
        <!-- Right: Tool Outputs -->
        <div class="chat-column">
            <div id="contextItems">
                <!-- Tool results go here -->
            </div>
        </div>
    </div>
</div>
```

### 2. Output View (for HTML reports)
```html
<div class="output-panel" id="outputPanel">
    <div class="output-placeholder">
        <!-- "Work products will appear here" -->
    </div>
    <iframe class="output-frame" id="outputFrame"></iframe>
</div>
```

Toggle buttons in the header switch between these views.

---

## Message Bubbles

Each message is a `<div>` with this structure:

```html
<div class="message agent">  <!-- or "user" -->
    <div class="message-avatar">
        <svg>...</svg>  <!-- Icon -->
    </div>
    <div class="message-content">
        <div class="message-bubble">
            <!-- The actual text -->
        </div>
        <div class="message-meta">
            Universal Agent • 14:32:08
        </div>
    </div>
</div>
```

**Agent messages** float left (cyan color).
**User messages** float right (magenta color).

---

## The Input Area

```html
<div class="panel input-panel">
    <div class="input-wrapper">
        <textarea class="chat-input" id="chatInput" 
                  placeholder="Transmit your request...">
        </textarea>
        <button class="send-btn">
            <svg><!-- Send icon --></svg>
        </button>
    </div>
</div>
```

JavaScript listens for:
- Click on send button
- Enter key (without Shift) in textarea

---

## Background Effects

These create the futuristic look but have no functional purpose:

```html
<div class="bg-grid"></div>           <!-- Animated grid pattern -->
<div class="bg-gradient-orb orb-1"></div>  <!-- Floating cyan blob -->
<div class="bg-gradient-orb orb-2"></div>  <!-- Floating magenta blob -->
<div class="particles" id="particles"></div>  <!-- Rising particles -->
```

The particles are created dynamically by JavaScript on page load.

---

## Next: 003 - CSS Styling
