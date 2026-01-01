# Setup and Configuration

**Last Updated**: 2025-12-31

Before running the bot (locally or on Railway), you need to create the bot in Telegram and configure your environment secrets.

---

## 1. Create the Bot (BotFather)

To create a bot, you need to talk to the **BotFather** on Telegram.

1.  **Open Telegram** on your phone or desktop.
2.  **Search** for `@BotFather`. Look for the blue verified checkmark.
3.  **Start** the chat and type `/newbot`.
4.  **Name**: Give your bot a display name (e.g., "Jarvis Agent").
5.  **Username**: Choose a unique username that ends in `bot` (e.g., `MyKevAgent_bot`).
6.  **Token**: BotFather will reply with a long string like:
    `123456789:ABCdefGHIjklMNOpqrsTUVwxys`
    
    👉 **Copy this token**. This is your `TELEGRAM_BOT_TOKEN`.

---

## 2. Get Your User ID

To ensure *only you* can use the bot, you need your unique numeric Telegram User ID.

1.  **Search** for `@userinfobot` on Telegram.
2.  **Start** the chat or type `/start`.
3.  It will reply with your info. Look for:
    `Id: 987654321`
    
    👉 **Copy this number**. This is your `ALLOWED_USER_IDS`.

---

## 3. Create a Webhook Secret

This is a password you create to secure the connection between Telegram and your specific server instance.

- It can be any random string.
- Example: `super-secure-secret-token-123`
- 👉 **Save this string**. This is your `WEBHOOK_SECRET`.

---

## 4. Environment Variables

If running locally, these go in `.env`. If deploying to Railway, these go in the Dashboard Variables.

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `ALLOWED_USER_IDS` | Comma-separated list of numeric user IDs |
| `WEBHOOK_SECRET` | Your random secret string |
| `WEBHOOK_URL` | Public URL of your bot + `/webhook` (e.g., `https://my-app.up.railway.app/webhook`) |
| `PORT` | (Optional) Port to listen on. Default `8000`. |
| `ANTHROPIC_API_KEY` | For the AI brain |
| `COMPOSIO_API_KEY` | For tool integrations |
