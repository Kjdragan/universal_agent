# 004 - JavaScript WebSocket Client

This document explains the JavaScript code that connects the UI to the Python backend.

---

## Overview

The JavaScript is at the bottom of `universal_agent_ui.html` inside `<script>` tags. It does:

1. **Connects** to the Python backend via WebSocket
2. **Sends** user queries as JSON messages
3. **Receives** streaming events (text, tool calls, etc.)
4. **Updates** the DOM to show responses

---

## The Connection

```javascript
const WS_URL = 'ws://localhost:8000/ws';
let ws = null;  // The WebSocket connection object

function connect() {
    ws = new WebSocket(WS_URL);
    
    ws.onopen = () => {
        // Connection established
        statusText.textContent = 'Connected';
    };
    
    ws.onclose = () => {
        // Connection lost - try to reconnect
        statusText.textContent = 'Disconnected';
        setTimeout(connect, 3000);  // Retry in 3 seconds
    };
    
    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
    };
    
    ws.onmessage = (event) => {
        // Received a message from server
        const msg = JSON.parse(event.data);
        handleServerMessage(msg);
    };
}

// Connect when page loads
connect();
```

### What's Happening?

1. **`new WebSocket(WS_URL)`** - Creates a connection to `ws://localhost:8000/ws`
2. **Event handlers** are functions that run when something happens:
   - `onopen` - Connection succeeded
   - `onclose` - Connection ended (we auto-reconnect)
   - `onerror` - Something went wrong
   - `onmessage` - Server sent us data

---

## Sending a Message

When the user clicks Send or presses Enter:

```javascript
function sendQuery() {
    const text = chatInput.value.trim();
    
    // Don't send empty messages
    if (!text) return;
    
    // Don't send if not connected
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    
    // Add user message to UI immediately
    messagesContainer.appendChild(createMessage(text, true));
    
    // Send to Python backend
    ws.send(JSON.stringify({ 
        type: 'query', 
        text: text 
    }));
    
    // Clear input
    chatInput.value = '';
    
    // Show "Processing" status
    statusIndicator.classList.add('processing');
}
```

### What's `JSON.stringify`?

JavaScript objects look like: `{ type: 'query', text: 'Hello' }`

`JSON.stringify()` converts this to a string: `'{"type":"query","text":"Hello"}'`

WebSocket can only send strings or binary, not JavaScript objects directly.

---

## Receiving Messages

All messages from the server go through `handleServerMessage`:

```javascript
function handleServerMessage(msg) {
    switch (msg.type) {
        case 'session_info':
            // Initial session data
            console.log('Session:', msg.data);
            break;
            
        case 'text':
            // Agent is speaking - append text
            // ...
            break;
            
        case 'tool_call':
            // Agent is using a tool
            // ...
            break;
            
        // ... more cases
    }
}
```

### What's `switch`?

It's like multiple `if` statements. Instead of:

```javascript
if (msg.type === 'text') { ... }
else if (msg.type === 'tool_call') { ... }
else if (msg.type === 'error') { ... }
```

We write:

```javascript
switch (msg.type) {
    case 'text': ... break;
    case 'tool_call': ... break;
    case 'error': ... break;
}
```

---

## Handling Text Events

The agent's response comes as multiple `text` events (streaming):

```javascript
case 'text':
    // Find the last agent message, or create new one
    let lastAgent = messagesContainer.querySelector('.message.agent:last-of-type');
    
    if (!lastAgent || lastAgent.dataset.complete === 'true') {
        // Need a new message bubble
        lastAgent = createMessage('');
        messagesContainer.appendChild(lastAgent);
    }
    
    // Append the new text
    const bubble = lastAgent.querySelector('.message-bubble');
    bubble.textContent += msg.data.text;
    
    // Scroll to bottom
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    break;
```

### What's `.querySelector()`?

It finds an element matching a CSS selector:
- `'.message.agent'` - Elements with both `message` and `agent` classes
- `':last-of-type'` - The last one of that type

---

## Handling Tool Calls

When the agent uses a tool:

```javascript
case 'tool_call':
    toolCallCount++;
    toolCallsEl.textContent = toolCallCount;  // Update counter
    
    statusIndicator.classList.add('processing');
    statusText.textContent = `Calling ${msg.data.name}...`;
    
    addActivity('TOOL', `${msg.data.name}: Processing...`);
    break;
```

---

## Creating DOM Elements

The `createMessage` function builds HTML dynamically:

```javascript
function createMessage(text, isUser = false) {
    const msg = document.createElement('div');
    msg.className = `message ${isUser ? 'user' : 'agent'}`;
    
    msg.innerHTML = `
        <div class="message-avatar">...</div>
        <div class="message-content">
            <div class="message-bubble">${text}</div>
            <div class="message-meta">...</div>
        </div>
    `;
    
    return msg;
}
```

### What's `.createElement()` and `.innerHTML`?

- **`document.createElement('div')`** - Creates a new `<div>` element
- **`msg.innerHTML = '...'`** - Sets the HTML content inside that element
- **Template literals** (backticks \`) let you embed variables: `${text}`

---

## View Toggle Functions

Switching between Chat and Output views:

```javascript
function showChatView() {
    chatView.classList.remove('hidden');
    outputPanel.classList.remove('active');
    chatViewBtn.classList.add('active');
    outputViewBtn.classList.remove('active');
}

function showOutputView() {
    chatView.classList.add('hidden');
    outputPanel.classList.add('active');
    chatViewBtn.classList.remove('active');
    outputViewBtn.classList.add('active');
}
```

### What's `.classList`?

Every element has a list of CSS classes. You can:
- `.add('classname')` - Add a class
- `.remove('classname')` - Remove a class
- `.toggle('classname')` - Add if missing, remove if present

CSS rules like `.hidden { display: none; }` then show/hide elements.

---

## Loading HTML into the Output Panel

```javascript
function loadOutputContent(htmlContent) {
    outputPlaceholder.style.display = 'none';  // Hide placeholder
    outputFrame.style.display = 'block';       // Show iframe
    outputFrame.srcdoc = htmlContent;          // Set HTML content
    showOutputView();                          // Switch to output view
}
```

### What's `srcdoc`?

An `<iframe>` normally loads a URL (`src="https://..."`)

`srcdoc` lets you set HTML content directly as a string - perfect for displaying generated reports without needing a separate URL.

---

## Event Listeners

```javascript
sendBtn.addEventListener('click', sendQuery);

chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();  // Don't add newline
        sendQuery();
    }
});
```

### What's `.addEventListener()`?

It attaches a function to run when something happens:
- `'click'` - User clicked the element
- `'keydown'` - User pressed a key
- `(e) => { ... }` - The function to run (e is the event object)

---

## Next: 005 - Event Types and Message Protocol
