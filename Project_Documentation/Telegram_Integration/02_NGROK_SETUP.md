# Ngrok Setup Guide

## What is Ngrok?
Ngrok is a tool that creates a secure tunnel from the public internet to your local machine. Since the Telegram servers cannot reach your computer directly (it's behind a firewall), Ngrok gives you a public URL (e.g., `https://xyz.ngrok-free.app`) that forwards traffic to your local bot.

## 1. Create an Ngrok Account
1.  Go to [dashboard.ngrok.com](https://dashboard.ngrok.com/signup).
2.  Sign up for a free account.

## 2. Install Ngrok (Linux/WSL)
Run the following commands in your terminal:
```bash
curl -O https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
sudo tar xvzf ngrok-v3-stable-linux-amd64.tgz -C /usr/local/bin
```
Verify installation:
```bash
ngrok version
```

## 3. Authenticate
1.  On the [Ngrok Dashboard](https://dashboard.ngrok.com/get-started/your-authtoken), copy your **Authtoken**.
2.  Run this command:
    ```bash
    ngrok config add-authtoken <YOUR_TOKEN>
    ```

## 4. Start the Tunnel
To start forwarding traffic to your bot (which will run on port 8000), run:
```bash
ngrok http 8000
```
This command will take over your terminal screen. **Do not close it.**

## 5. Get Your Webhook URL
Look for the `Forwarding` line in the Ngrok screen:
`Forwarding https://a1b2-c3d4.ngrok-free.app -> http://localhost:8000`

1.  Copy the URL: `https://a1b2-c3d4.ngrok-free.app`
2.  Append `/webhook`: `https://a1b2-c3d4.ngrok-free.app/webhook`
3.  👉 **This is your `WEBHOOK_URL`**. It changes every time you restart Ngrok (on the free plan).

## 6. Update .env
Update `WEBHOOK_URL` in your `.env` file whenever you restart Ngrok.
