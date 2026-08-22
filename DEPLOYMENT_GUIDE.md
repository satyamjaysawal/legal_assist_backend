## Git Commit → Deployment Steps

### 1. Verify locally

```powershell
# Backend
cd "C:\Users\Dell 5400\OneDrive\Desktop\legal_ai_assistant\legal_assist_backend"
.\.venv\Scripts\python.exe -m compileall -q main.py core services agents connectors websocket
.\.venv\Scripts\python.exe -m pytest tests -q

# Frontend
cd "..\legal_assist_frontend"
npm run build
```

### 2. Commit + push Backend

```powershell
cd "C:\Users\Dell 5400\OneDrive\Desktop\legal_ai_assistant\legal_assist_backend"

git status --short
git diff --check
git diff

git add <changed-files>
git commit -m "Describe the backend change"
git push origin main

git status
```

### 3. Commit + push Frontend

```powershell
cd "C:\Users\Dell 5400\OneDrive\Desktop\legal_ai_assistant\legal_assist_frontend"

git status --short
git diff --check
git diff

git add <changed-files>
git commit -m "Describe the frontend change"
git push origin main

git status
```

**Important:** Both repos must be **clean** and the commits must be **pushed to `main` before any Vercel deployment**.

### 4. Deploy Backend

```powershell
cd "C:\Users\Dell 5400\OneDrive\Desktop\legal_ai_assistant\legal_assist_backend"

vercel --prod --yes --no-wait --scope team_NKnxGvq9toz7xCCe6RahKPeE
```

Then:

```powershell
vercel inspect <backend-deployment-url>
```

Wait for **Ready**.

### 5. Deploy Frontend

```powershell
cd "C:\Users\Dell 5400\OneDrive\Desktop\legal_ai_assistant\legal_assist_frontend"

vercel --prod --yes --no-wait --scope team_NKnxGvq9toz7xCCe6RahKPeE
```

Then:

```powershell
vercel inspect <frontend-deployment-url>
```

Wait for **Ready**.

### 6. Reassign canonical aliases

```powershell
vercel alias <backend-deployment-url> legal-assist-api.vercel.app
vercel alias <frontend-deployment-url> legal-assist-agentic.vercel.app
```

### 7. Remove stray aliases

```powershell
vercel alias list | Select-String "legal-assist"
```

Only these should remain:

```text
legal-assist-api.vercel.app
legal-assist-agentic.vercel.app
```

### 8. Final production verification

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri "https://legal-assist-api.vercel.app/health"

Invoke-WebRequest -UseBasicParsing `
  -Uri "https://legal-assist-agentic.vercel.app"
```

**Release order:**
**Test → Commit → Push → Clean Git → Backend Deploy → Frontend Deploy → Alias → Verify**.
