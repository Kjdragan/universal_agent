# Email & Identity Resolution

## "Me" and User Alias Resolution

When the user says things like:
- "email it to **me**"
- "send to **my email**"
- "gmail **me** the report"

**You should use "me" as the recipient.** The system will automatically resolve it.

### Correct Approach
```json
{
  "recipient_email": "me",
  "subject": "Your Report",
  "body": "See attached."
}
```

The identity system will resolve "me" to the user's actual email address (e.g., `kevin.dragan@outlook.com`).

### Available Aliases
The following aliases can be used as recipients:
- `me` → User's primary email
- `my email` → User's primary email
- `my gmail` → User's Gmail address (if configured)
- `my outlook` → User's Outlook address (if configured)

### Important Rules
1. **NEVER ask for the user's email** if they said "me" or "to me"
2. Use the alias directly in `recipient_email` or `to`
3. The pre-tool hook will resolve it automatically

### Example: Full Email Flow
1. Generate HTML report
2. Convert to PDF via Chrome headless
3. Upload PDF: `upload_to_composio(path, tool_slug='GMAIL_SEND_EMAIL', toolkit_slug='gmail')`
4. Send email: `GMAIL_SEND_EMAIL({recipient_email: 'me', subject: '...', body: '...', attachment: {...}})`

The email will be sent to the user's configured address.
