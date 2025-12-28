# Telegram Bot Setup Guide

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

- It can be any string.
- Example: `super-secure-secret-token-123`
- 👉 **Save this string**. This is your `WEBHOOK_SECRET`.

## 4. Save Credentials
Add these values to your `.env` file in the project directory:

```env
TELEGRAM_BOT_TOKEN="your_token_here"
ALLOWED_USER_IDS="your_id_here"
WEBHOOK_SECRET="your_secret_here"
```
