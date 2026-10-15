# Before Phase 0: one-time setup on your PC

Run these in **PowerShell**. Docker's WSL step needs an **Administrator**
PowerShell window. Everything else works in a normal one.

## 1. WSL2 (needed by Docker Desktop)

In an Administrator window:

```powershell
wsl --install
```

Restart Windows if it asks you to.

## 2. Docker Desktop, Node.js LTS and the GitHub CLI

```powershell
winget install --id Docker.DockerDesktop -e
```

```powershell
winget install --id OpenJS.NodeJS.LTS -e
```

```powershell
winget install --id GitHub.cli -e
```

Then close and reopen PowerShell so the new commands are on your PATH. Start
Docker Desktop once from the Start menu and accept its setup. Docker Desktop
is free for personal use.

## 3. Check everything is there

```powershell
docker --version; docker compose version; node --version; npm --version; gh --version
```

`node --version` should print `v24.x`.

## 4. Sign in to GitHub and create the empty repo

```powershell
gh auth login
```

Choose GitHub.com, HTTPS, and log in with a browser. Then create the repo
**without** any README or license. Phase 0 pushes the first commit:

```powershell
gh repo create smart-money-tracker --private
```

Use `--public` instead if you want it public. Tell me which one you chose.

## 5. Free API keys (can wait until the phase that needs them)

You create each of these yourself, because signing up accepts that service's
terms. Put the keys in `.env` later, never in chat.

| Service | Sign-up page | Needed by |
|---|---|---|
| OpenFIGI | https://www.openfigi.com/api | Phase 3 |
| api.data.gov (Congress.gov, FEC) | https://api.data.gov/signup/ | Phase 5 |
| Lobbying Disclosure (lda.gov) | https://lda.gov/api/register/ | Phase 5 |
| Finnhub (private-mode quotes only) | https://finnhub.io/register | optional |
