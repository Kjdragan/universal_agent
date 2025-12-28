# Telegram Bot Usage Guide

## Commands
Once your bot is running, you can use these commands in the chat:

- **/start**: Checks if the bot is online and authorized to talk to you.
- **/status**: Shows the current task queue and the status of recent tasks (Pending, Running, Completed).
- **/agent [prompt]**: Sends a task to the Universal Agent.
  - Example: `/agent research the latest AI news and summarize it`
  - Example: `/agent create a python script to calculate fibonacci numbers`

## Workflow
1.  **Send Request**: You send `/agent <your request>`.
2.  **Confirmation**: The bot replies "✅ Task Queued: `task_id`".
3.  **Processing**: The agent picks up the task.
    - If you used `/status`, you'll see it switch to "RUNNING".
4.  **Updates**: When the task starts and finishes, the bot sends you a notification.
5.  **Result**:
    - Small results are sent as text messages.
    - Large results (or if errors occur) include a `task_uuid.log` file attachment with the full execution log.

## Troubleshooting
- **Bot doesn't reply**:
  - Check if `ngrok` is running.
  - Check if `docker` container is running (`docker ps`).
  - Did the `WEBHOOK_URL` change? Run `register_webhook.py` again.
- **"Unauthorized access"**:
  - Your Telegram ID is not in `ALLOWED_USER_IDS` in `.env`.
- **Agent errors**:
  - Check the attached log file or run `docker logs universal_agent_bot`.
