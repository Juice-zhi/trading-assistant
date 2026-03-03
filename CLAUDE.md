# Claude Development Rules

## GitHub Push Policy

Every time a change is made to the project, push it to GitHub immediately after completion.
If the remote repository does not exist, create it first, then push.

```bash
git add <changed files>
git commit -m "..."
git push origin main
```

Never leave changes committed locally without pushing.

## Restart After Update

After every update, kill any running instance and start a fresh one:

```bash
pkill -f "python main.py"; sleep 1; python /Users/guozhi/Agent/trading_assistant/main.py
```
