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

## Deploy to Render.com (recommended — free)

### Prerequisites
- GitHub account
- Render account (free at render.com)
- Gemini API key (free at [aistudio.google.com](https://aistudio.google.com)) or OpenAI key

### Steps

**1. Fork / push this repo to GitHub**

**2. Connect to Render**
- Go to [render.com/dashboard](https://dashboard.render.com)
- Click **New +** → **Blueprint**
- Connect your GitHub repo
- Render reads `render.yaml` and creates the web service + Redis automatically

**3. Set secrets in the Render dashboard**

Go to your service → **Environment** and add:

| Key | Value |
|---|---|
| `AI_GRADER_API_KEY_GOOGLE` | Your Gemini API key |
| `LTI_OPENEDX_KEY` | Consumer key you'll enter in Open edX (e.g. `mygrader`) |
| `LTI_OPENEDX_SECRET` | A long random secret string |
| `LTI_MOODLE_KEY` | Consumer key for Moodle (e.g. `mygrader`) |
| `LTI_MOODLE_SECRET` | A long random secret string |
| `ALLOWED_ORIGINS` | `https://yourlms.com,https://studio.yourlms.com` |
| `LTI_ALLOWED_DOMAINS` | `yourlms.com` |

**4. Note the service URL**

Your app will be at `https://lti-ai-grader-xxxx.onrender.com`

> ⚠️ **Free tier note:** Render's free web service sleeps after 15 min of inactivity (cold start ~30 s). If your LMS has a short LTI launch timeout, upgrade to the **Starter** plan ($7/mo) or use Railway instead.

---

## Deploy to Railway (alternative — $5 free credit/month)

**1. Push repo to GitHub**

**2. New project → Deploy from GitHub repo**

**3. Add a Redis service:**  
   New → Database → Redis

**4. Set environment variables** (same list as Render above, plus `REDIS_URL` from the Redis service)

**5. Your app is live** — no cold starts on Railway.

---

## Configure your LMS

### Open edX

1. **Settings → Advanced Settings** → add `lti_consumer` to *Advanced module list*
2. **LTI Passports** → add: `mygrader:LTI_OPENEDX_KEY:LTI_OPENEDX_SECRET`
3. In a unit → **Advanced** → **LTI Consumer**:
   - **LTI ID:** `mygrader`
   - **LTI URL:** `https://your-app.onrender.com/lti-launch?file=/C1-writing-correction-LTI.html`
   - **LTI version:** 1.1/1.2
   - **Scored:** True
   - **Weight:** 5 (or your max score)

### Moodle

1. **Site administration → Plugins → Activity modules → External tool → Manage tools**
2. Add tool:
   - **Tool URL:** `https://your-app.onrender.com/lti-launch?file=/C1-writing-correction-LTI.html`
   - **Consumer key:** value of `LTI_MOODLE_KEY`
   - **Shared secret:** value of `LTI_MOODLE_SECRET`
   - **Send grades:** Yes

---

## Local development

```bash
# 1. Clone and install
git clone https://github.com/yourname/lti-ai-grader-cloud
cd lti-ai-grader-cloud
pip install -r requirements.txt

# 2. Start a local Redis (Docker)
docker run -d -p 6379:6379 redis:7-alpine

# 3. Configure
cp .env.example .env
# Edit .env with your keys

# 4. Run
python -m dotenv run python app.py
# or
flask --app app run --debug
```

Visit `http://localhost:5000` — you should see the health-check page.  
The grader HTML is at `http://localhost:5000/C1-writing-correction-LTI.html`.

---

## File structure

```
lti-ai-grader-cloud/
├── app.py                    # Flask app: routes /lti-launch and /grade
├── aigrader_engine.py        # Pure grading logic (AI call, OAuth, grade passback)
├── grader_config.py          # Grader config read from env vars
│                             # Edit system_instructions here to change the prompt
├── static/
│   └── aigrader.js           # Frontend: form, feedback display, Open edX postMessage
├── templates/
│   └── C1-writing-correction-LTI.html   # Edit CONFIG here for your exam
├── javascript-for-openedx.html           # Open edX task-injection helper
├── .env.example              # Environment variable template
├── requirements.txt
├── render.yaml               # Render.com one-click deploy config
├── Procfile                  # Railway / Heroku process config
└── README.md
```

---

## Adding a new exam (different prompt or task)

1. **Edit the prompt** in `grader_config.py` → `GRADER_SYSTEM_INSTRUCTIONS`  
   (or set `GRADER_SYSTEM_INSTRUCTIONS` as an env var for the production deployment)

2. **Edit the task** in `templates/C1-writing-correction-LTI.html` → `CONFIG.taskHTML` and `CONFIG.initialValue`

3. **For multiple simultaneous exams**, duplicate the HTML file (e.g. `B2-writing-LTI.html`) and point each LTI component to a different `?file=` value. A single deployment serves all of them.

---

## Dynamic task injection from Open edX

To set different task descriptions per unit without editing the HTML:

1. Add a **Text component** immediately *before* the LTI component in the unit
2. Use the HTML editor to paste the content of `javascript-for-openedx.html`
3. Replace the `taskText` variable with your specific task

This lets a single deployed grader serve many different writing tasks across a course.

---

## Troubleshooting

**Grade not appearing in the LMS gradebook**  
- Check that `LTI_OPENEDX_KEY` / `LTI_OPENEDX_SECRET` match exactly what is in the LMS LTI passport.  
- Verify `LTI_ALLOWED_DOMAINS` includes your LMS domain.  
- Set `DEBUG=true` temporarily and check the Render logs.

**Cold start / timeout on LTI launch**  
- Upgrade Render to Starter plan, or switch to Railway.

**Redis connection error on startup**  
- Make sure the Redis service is running and `REDIS_URL` is set correctly.  
- On Render, use the **Internal** Redis URL (not the external one).

**AI API error**  
- Verify `AI_GRADER_API_KEY_GOOGLE` is valid and the model name (`AI_GRADER_MODEL_NAME`) is correct.
- Gemini free tier has rate limits; consider `gemini-2.5-flash-lite` for lowest latency.

---

## License

Apache-2.0 — same as the original project.
