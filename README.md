# LTI AI Grader – Cloud Edition

Flexible LTI 1.1 AI grader with configurable prompt and LLM.  
**No Linux server required.** Deploy in minutes to Render.com or Railway (both have free tiers).

Tested with Open edX and Moodle.  
Based on [lti-ai-grader](https://github.com/moocupv/lti-ai-grader) by moocupv.

---

## How it works

```
LMS (Open edX / Moodle)
    │  POST LTI 1.1 launch
    ▼
/lti-launch  (Flask)
    │  saves token → Redis (TTL 1 h)
    │  redirects browser
    ▼
C1-writing-correction-LTI.html  ←  aigrader.js
    │  student writes, clicks Submit
    │  POST JSON {studentInput, session_token, …}
    ▼
/grade  (Flask)
    │  reads session from Redis
    │  calls Gemini / OpenAI API
    │  parses grade with regex
    │  sends grade back to LMS (OAuth 1.0a + XML)
    └─ returns JSON {feedback, score_info, lti_notified}
```

---

## Security

All security mechanisms from the original CGI version are preserved:

| Mechanism | Original | Cloud edition |
|---|---|---|
| Origin whitelist | `ALLOWED_ORIGINS` in `.py` | `ALLOWED_ORIGINS` env var |
| Rate limiting | Nginx `limit_req` 20r/m, burst 3 | `flask-limiter` + Redis, same values |
| Path traversal protection | `os.path.basename()` | Identical, plus extension whitelist |
| Session integrity | JSON files in `/var/secure/` | Redis with key prefix + TTL |
| LTI domain whitelist | `LTI_ALLOWED_DOMAINS` in `.py` | `LTI_ALLOWED_DOMAINS` env var |
| OAuth 1.0a grade passback | HMAC-SHA1 in `aigrader.py` | Identical code in `aigrader_engine.py` |
| Secrets isolation | `/var/secure/aigrader.env` (file, 640) | Environment variables in platform dashboard |
| HTTPS | Nginx config | Enforced by Render / Railway at the edge |
| Debug mode | `DEBUG = False` in `.py` | `DEBUG=false` env var |

**Improvements over the original:**
- Rate limiting is distributed (works across multiple Gunicorn workers via Redis), not per-process.
- No filesystem sessions → no path traversal risk on session files.
- Secrets never touch disk on the server.

---

## File structure

```
lti-ai-grader-cloud/
├── app.py                             # Flask app: routes /lti-launch and /grade
├── aigrader_engine.py                 # Pure grading logic (AI call, OAuth, grade passback)
├── grader_config.py                   # Grader config read from env vars
│                                      # Edit system_instructions here to change the prompt
├── static/
│   └── aigrader.js                    # Frontend: form, feedback display, Open edX postMessage
├── templates/
│   └── C1-writing-correction-LTI.html # Edit CONFIG here for your exam
├── javascript-for-openedx.html        # Open edX task-injection helper
├── .env.example                       # Environment variable template
├── requirements.txt
├── render.yaml                        # Render.com one-click deploy config
├── Procfile                           # Railway / Heroku process config
└── README.md
```

---

## Option 1 — Deploy to Render.com (free, easiest)

### Prerequisites
- GitHub account with this repo
- Render account (free at [render.com](https://render.com))
- An API key from one of the supported providers (see table below)

### Steps

**1. Connect to Render**

- Go to [dashboard.render.com](https://dashboard.render.com)
- Click **New +** → **Blueprint**
- Connect your GitHub account and select this repository
- Render reads `render.yaml` and creates the web service + Redis automatically

**2. Set secrets in the Render dashboard**

Go to your web service → **Environment** tab and add the variables for your chosen AI provider:

**Option A — Google Gemini** (free tier available at [aistudio.google.com](https://aistudio.google.com))

| Key | Value |
|---|---|
| `AI_GRADER_PROVIDER` | `google` |
| `AI_GRADER_API_KEY_GOOGLE` | Your Gemini API key |
| `AI_GRADER_MODEL_NAME` | `gemini-2.5-flash-lite` |

**Option B — OpenAI**

| Key | Value |
|---|---|
| `AI_GRADER_PROVIDER` | `openai` |
| `AI_GRADER_API_KEY_OPENAI` | Your OpenAI API key (`sk-...`) |
| `AI_GRADER_MODEL_NAME` | `gpt-4o-mini` |

**Option C — Any OpenAI-compatible API** (Azure, Mistral, Ollama, LM Studio, OpenRouter, etc.)

| Key | Value |
|---|---|
| `AI_GRADER_PROVIDER` | `openai` |
| `AI_GRADER_API_KEY_OPENAI` | Your API key for that provider |
| `AI_GRADER_MODEL_NAME` | Model name as required by that provider |
| `AI_GRADER_API_URL` | Base URL of the provider (e.g. `https://api.mistral.ai`, `http://localhost:11434`) |

Then add the following for all options:

| Key | Value |
|---|---|
| `LTI_OPENEDX_KEY` | Consumer key you will enter in Open edX (e.g. `openedx_key`) |
| `LTI_OPENEDX_SECRET` | A long random secret string |
| `LTI_MOODLE_KEY` | Consumer key for Moodle (e.g. `moodle_key`) |
| `LTI_MOODLE_SECRET` | A long random secret string |
| `ALLOWED_ORIGINS` | `https://yourlms.com,https://studio.yourlms.com` |
| `LTI_ALLOWED_DOMAINS` | `yourlms.com` |

**3. Get your URL**

Your app will be at a URL like `https://lti-ai-grader-xxxx.onrender.com`

> ⚠️ **Free tier note:** Render's free web service sleeps after 15 min of inactivity and takes ~30 s to wake up. If your LMS has a short LTI launch timeout this can cause errors. Upgrade to the **Starter** plan ($7/mo) or use Railway (Option 2) to avoid this.

---

## Option 2 — Deploy to Railway (recommended, $5 free credit/month)

Railway does not have cold starts — the service is always on. The free credit covers moderate educational use (up to ~100 evaluations/day). If you exceed it, the Hobby plan is $5/month flat. The public URL uses HTTPS automatically — no configuration needed.

### Step 1 — Create account

Go to [railway.app](https://railway.app) and sign up **with your GitHub account**. This is important because you will connect your repo directly.

### Step 2 — Grant GitHub permissions

When you click **New Project → Deploy from GitHub repo**, Railway may show "No repositories found". This means it does not yet have permission to see your repos:

- Click **Configure GitHub App**
- GitHub opens a permissions page → click **Install & Authorize**
- Choose your account
- Under **Repository access** select **Only select repositories** and tick `lti-ai-grader-no-linux`
- Click **Save**

Back in Railway, click **Refresh** and the repository will appear.

### Step 3 — Create the project

- Click **New Project**
- Choose **Deploy from GitHub repo**
- Select this repository (`lti-ai-grader-no-linux`)
- Railway detects the `Procfile` automatically and starts building

Do not close the page — continue to the next step while it builds.

### Step 4 — Add Redis

Inside the same project:

- Click **+ New** (top right, inside the project view)
- Choose **Database → Add Redis**
- Railway creates the Redis service and connects it internally to your project

Once created, click on the Redis service → **Variables** tab → copy the value of `REDIS_URL`. You will need it in the next step.

### Step 5 — Set environment variables

Click on your web service (the one with the code) → **Variables** tab → **Raw Editor** and paste the block for your chosen AI provider, then add the common variables below it.

**Option A — Google Gemini**

```
AI_GRADER_PROVIDER=google
AI_GRADER_API_KEY_GOOGLE=your-gemini-api-key
AI_GRADER_MODEL_NAME=gemini-2.5-flash-lite
```

**Option B — OpenAI**

```
AI_GRADER_PROVIDER=openai
AI_GRADER_API_KEY_OPENAI=sk-your-openai-key
AI_GRADER_MODEL_NAME=gpt-4o-mini
```

**Option C — Any OpenAI-compatible API** (Azure, Mistral, Ollama, OpenRouter, etc.)

```
AI_GRADER_PROVIDER=openai
AI_GRADER_API_KEY_OPENAI=your-api-key-for-that-provider
AI_GRADER_MODEL_NAME=model-name-as-required-by-that-provider
AI_GRADER_API_URL=https://base-url-of-the-provider.com
```

**Common variables — add these regardless of provider:**

```
LTI_OPENEDX_KEY=openedx_key
LTI_OPENEDX_SECRET=a-long-random-secret
LTI_MOODLE_KEY=moodle_key
LTI_MOODLE_SECRET=another-long-random-secret
ALLOWED_ORIGINS=https://yourlms.com,https://studio.yourlms.com
LTI_ALLOWED_DOMAINS=yourlms.com
REDIS_URL=redis://... (paste the value copied in Step 4)
SESSION_TIMEOUT=3600
SEND_GRADE_TO_LMS=true
DEBUG=false
```

Click **Deploy** — Railway redeploys automatically.

### Step 6 — Get your public URL

In your web service → **Settings** tab → **Networking** section → click **Generate Domain**.

You will get a URL like:
```
https://lti-ai-grader-no-linux-production.up.railway.app
```

This is the URL you will use when configuring the LMS.

---

## Configure your LMS

### Open edX

1. **Settings → Advanced Settings** → add `lti_consumer` to the *Advanced module list*
2. **LTI Passports** → add one line per LMS key in this format:
   ```
   openedx_key:openedx_key:a-long-random-secret
   ```
3. In a unit → **Advanced** → **LTI Consumer**:
   - **LTI ID:** `openedx_key`
   - **LTI URL:** `https://your-app-url.com/lti-launch?file=C1-writing-correction-LTI.html`
   - **LTI version:** 1.1/1.2
   - **Scored:** True
   - **Weight:** 5 (or your maximum score)

### Moodle

1. **Site administration → Plugins → Activity modules → External tool → Manage tools**
2. Add tool:
   - **Tool URL:** `https://your-app-url.com/lti-launch?file=C1-writing-correction-LTI.html`
   - **Consumer key:** value of `LTI_MOODLE_KEY`
   - **Shared secret:** value of `LTI_MOODLE_SECRET`
   - **Send grades:** Yes

---

## Local development

```bash
# 1. Clone and install
git clone https://github.com/moocupv/lti-ai-grader-no-linux
cd lti-ai-grader-no-linux
pip install -r requirements.txt

# 2. Start a local Redis (requires Docker)
docker run -d -p 6379:6379 redis:7-alpine

# 3. Configure
cp .env.example .env
# Edit .env with your keys

# 4. Run
flask --app app run --debug
```

Visit `http://localhost:5000` — you should see the health-check page.  
The grader HTML is at `http://localhost:5000/C1-writing-correction-LTI.html`.

---

## Adapting to a different exam

**Change the grading prompt:**  
Edit `grader_config.py` → `GRADER_SYSTEM_INSTRUCTIONS`. The prompt must instruct the LLM to output a line matching the `grade_identifier` pattern, for example:
```
FINAL_GRADE: 3/5
```

**Change the task shown to students:**  
Edit `templates/C1-writing-correction-LTI.html` → the `CONFIG` object at the top:
- `taskHTML` — the task instructions shown above the textarea
- `initialValue` — the default text pre-filled in the textarea
- `title` — the page title

**Multiple exams from a single deployment:**  
Duplicate the HTML file (e.g. `B2-writing-LTI.html`) and point each LTI component to a different `?file=` value. A single deployment serves all of them.

---

## Dynamic task injection from Open edX

To set different task descriptions per unit without editing the HTML:

1. Add a **Text component** immediately *before* the LTI component in the unit
2. Use the HTML editor to paste the content of `javascript-for-openedx.html`
3. Edit the `taskText` variable with your specific task

This lets a single deployed grader serve many different writing tasks across a course.

---

## Troubleshooting

**Grade not appearing in the LMS gradebook**
- Check that the LTI key and secret match exactly what is configured in the LMS.
- Verify `LTI_ALLOWED_DOMAINS` includes your LMS domain.
- Set `DEBUG=true` temporarily and check the platform logs.

**Cold start / timeout on LTI launch (Render only)**
- Upgrade Render to Starter plan, or switch to Railway.

**Redis connection error on startup**
- Make sure the Redis service is running and `REDIS_URL` is set correctly.
- On Render, use the **Internal** Redis URL (not the external one).
- On Railway, copy the `REDIS_URL` from the Redis service Variables tab.

**Railway shows "No repositories found"**
- Click **Configure GitHub App** and follow the permissions steps in Step 2 above.

**AI API error**
- Verify your API key is valid and the model name is correct.
- Gemini free tier has rate limits; `gemini-2.5-flash-lite` offers the lowest latency.
- For OpenAI-compatible providers, make sure `AI_GRADER_API_URL` points to the correct base URL and that the model name matches exactly what that provider expects.

---

## License

Apache-2.0 — same as the original project.
