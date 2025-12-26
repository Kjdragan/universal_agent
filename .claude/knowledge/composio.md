# Composio Tool Knowledge

## GMAIL_SEND_EMAIL

**Attachment format (CRITICAL)**:
- `attachment` must be a **DICT**, not a list
- Format: `{"name": str, "mimetype": str, "s3key": str}`
- Get `s3key` from `upload_to_composio` first

**Correct Example:**
```json
{
  "recipient": "user@example.com",
  "subject": "Report",
  "body": "See attached.",
  "attachment": {
    "name": "report.pdf",
    "mimetype": "application/pdf",
    "s3key": "215406/gmail/GMAIL_SEND_EMAIL/report.pdf"
  }
}
```

**Common Mistake (WRONG):**
```json
{
  "attachment": [{"name": "report.pdf", ...}]  // ❌ List format fails!
}
```

## upload_to_composio

- Returns the `s3key` needed for `GMAIL_SEND_EMAIL` attachments
- Always call this BEFORE attempting to send email with attachments
- The returned `s3key` is used directly in the attachment dict
