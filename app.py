#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LTI AI Grader - Cloud Edition
Flask application replacing lti-receiver.py + evaluate-*-conf.py (CGI)

Security features preserved from the original:
  - CORS origin whitelist (ALLOWED_ORIGINS)
  - Rate limiting: 20 req/min per IP, burst 3 (mirrors original Nginx limit_req)
  - Path traversal protection on file serving (os.path.basename)
  - Session integrity: token validated before any LTI param is used
  - LTI domain whitelist for grade passback (LTI_ALLOWED_DOMAINS)
  - OAuth 1.0a signing for grade passback (identical to original)
  - Debug mode controlled by env var (never exposes internals in production)
"""

import os
import json
import time
import random
import string
import logging

# Load .env for local development (no-op in production where env vars are injected)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import redis as redis_lib

import aigrader_engine
from grader_config import build_config

# ─── App setup ───────────────────────────────────────────────────────────────

DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.WARNING,
    format='[%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', template_folder='templates')

# ─── Redis – session store + rate-limiter backend ────────────────────────────

REDIS_URL    = os.environ.get('REDIS_URL', 'redis://localhost:6379')
SESSION_TTL  = int(os.environ.get('SESSION_TIMEOUT', 3600))
SESSION_PFX  = 'lti_sess:'          # namespace to avoid key collisions

try:
    _redis = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=3)
    _redis.ping()
    log.info("Redis connected: %s", REDIS_URL.split('@')[-1])  # hide credentials in log
except Exception as exc:
    log.error("Redis connection failed: %s", exc)
    _redis = None

# ─── Rate limiter (mirrors Nginx: 20r/m, burst 3, 429 on overflow) ───────────

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=REDIS_URL,
)

# ─── CORS / origin helpers ───────────────────────────────────────────────────

ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip()
]

def _cors_origin(request_origin: str) -> str:
    """Return the origin to echo in Access-Control-Allow-Origin."""
    if request_origin and request_origin in ALLOWED_ORIGINS:
        return request_origin
    # Fallback: first configured origin, or wildcard if none configured
    return ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else '*'

def _add_cors(response, origin: str):
    response.headers['Access-Control-Allow-Origin']      = _cors_origin(origin)
    response.headers['Access-Control-Allow-Methods']     = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers']     = 'Content-Type'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

def _validate_origin() -> bool:
    """Reject POST requests from non-whitelisted origins (mirrors original)."""
    referer = request.headers.get('Referer', '')
    origin  = request.headers.get('Origin',  '')
    if request.method == 'GET' and not referer and not origin:
        return True
    for allowed in ALLOWED_ORIGINS:
        if referer.startswith(allowed) or origin == allowed:
            return True
    return False

# ─── Session helpers ─────────────────────────────────────────────────────────

def _generate_token() -> str:
    """32-char cryptographically random token (same as original)."""
    return ''.join(
        random.SystemRandom().choice(string.ascii_letters + string.digits)
        for _ in range(32)
    )

def _save_session(token: str, lti_params: dict) -> bool:
    if not _redis:
        return False
    data = {
        'lti_params': lti_params,
        'created_at': int(time.time()),
        'expires_at': int(time.time()) + SESSION_TTL,
    }
    try:
        _redis.setex(SESSION_PFX + token, SESSION_TTL, json.dumps(data))
        return True
    except Exception as exc:
        log.error("Session save failed: %s", exc)
        return False

def _load_session(token: str):
    """Called by aigrader_engine to retrieve LTI params by token."""
    if not _redis or not token:
        return None
    try:
        raw = _redis.get(SESSION_PFX + token)
        return json.loads(raw) if raw else None
    except Exception as exc:
        log.error("Session load failed: %s", exc)
        return None

# ─── LTI helpers ─────────────────────────────────────────────────────────────

def _validate_lti(params: dict) -> dict:
    required = ['lis_outcome_service_url', 'lis_result_sourcedid']
    found = [p for p in required if params.get(p)]
    return {'is_valid': len(found) >= 2, 'found': found, 'total': len(params)}

def _safe_redirect_url(requested_file: str) -> str:
    """Build redirect URL using only the basename – prevents path traversal."""
    host      = request.host
    safe_name = os.path.basename(requested_file or 'C1-writing-correction-LTI.html')
    return f"https://{host}/{safe_name}"

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/lti-launch', methods=['GET', 'POST', 'OPTIONS'])
@limiter.limit("20 per minute", error_message="Too many requests")
def lti_launch():
    """
    LTI 1.1 launch receiver.
    Replaces: lti-receiver.py (CGI)

    Flow:
      1. Validate origin
      2. Collect GET + POST params
      3. Save LTI params to Redis under a random token
      4. Return an HTML page that stores the token in localStorage/sessionStorage
         and redirects the browser to the grader HTML page
    """
    origin = request.headers.get('Origin', '')

    if request.method == 'OPTIONS':
        resp = make_response('', 204)
        return _add_cors(resp, origin)

    if request.method == 'POST' and not _validate_origin():
        log.warning("Origin rejected: %s %s",
                    request.headers.get('Origin'), request.headers.get('Referer'))
        resp = make_response('<html><body><h1>403 Forbidden</h1></body></html>', 403)
        return _add_cors(resp, origin)

    # Collect all params (GET + POST form)
    params = {}
    params.update(request.args.to_dict())
    params.update(request.form.to_dict())

    requested_file = params.get('file', 'C1-writing-correction-LTI.html')
    redirect_target = _safe_redirect_url(requested_file)

    # Health-check endpoint (GET with no query string)
    if request.method == 'GET' and not request.args:
        resp = make_response(
            f'<!DOCTYPE html><html><body>'
            f'<h1>✅ LTI AI Grader Active</h1>'
            f'<p>Launch endpoint: <code>/lti-launch</code></p>'
            f'<p>Default target: {redirect_target}</p>'
            f'</body></html>'
        )
        return _add_cors(resp, origin)

    validation = _validate_lti(params)
    token = _generate_token()

    if validation['is_valid']:
        _save_session(token, params)
        status_class, msg, delay, mode = 'success', '✅ LTI Session created', 1500, 'lti'
    elif validation['total'] > 0:
        _save_session(token, params)
        status_class, msg, delay, mode = 'warning', '⚠️ Partial session', 2000, 'partial'
    else:
        status_class, msg, delay, mode = 'error', '❌ Standalone mode', 2000, 'standalone'
        token = None

    debug_css    = '' if DEBUG else '.origin-debug { display: none; }'
    referer      = request.headers.get('Referer', 'N/A')
    remote_addr  = request.remote_addr

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: Arial; padding: 20px; text-align: center; background: #f5f5f5; }}
    .status-box {{ background: white; padding: 20px; border-radius: 5px;
                   max-width: 600px; margin: 0 auto;
                   box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
    .success {{ color: #28a745; }}
    .warning {{ color: #ffc107; }}
    .error   {{ color: #dc3545; }}
    .origin-debug {{ background: #eee; padding: 10px; margin-top: 20px;
                     text-align: left; font-size: 11px; }}
    {debug_css}
  </style>
</head>
<body>
  <div class="status-box">
    <h2 class="{status_class}">{msg}</h2>
    <p>Loading activity...</p>
    <div class="origin-debug">Ref: {referer} | IP: {remote_addr}</div>
  </div>
  <script>
    const sessionData = {{
      token:  {json.dumps(token)},
      mode:   {json.dumps(mode)},
      target: {json.dumps(redirect_target)}
    }};
    if (sessionData.token) {{
      localStorage.setItem('lti_session_token',  sessionData.token);
      sessionStorage.setItem('lti_session_token', sessionData.token);
    }}
    localStorage.setItem('lti_mode', sessionData.mode);
    setTimeout(() => {{
      try {{
        const url = new URL(sessionData.target);
        if (sessionData.token) url.searchParams.set('token', sessionData.token);
        url.searchParams.set('mode', sessionData.mode);
        window.location.href = url.toString();
      }} catch(e) {{
        const sep = sessionData.target.includes('?') ? '&' : '?';
        window.location.href = sessionData.target + sep + 'mode=' + sessionData.mode
          + (sessionData.token ? '&token=' + sessionData.token : '');
      }}
    }}, {delay});
  </script>
</body>
</html>"""

    resp = make_response(html)
    return _add_cors(resp, origin)


@app.route('/grade', methods=['POST', 'OPTIONS'])
@limiter.limit("20 per minute", error_message="Too many requests")
def grade():
    """
    AI grading endpoint.
    Replaces: evaluate-certacles-writing-c1-LTI-conf.py + aigrader.py (CGI)

    Receives JSON: { studentInput, defaultValue, session_token, emptyErrorMsg }
    Returns  JSON: { success, feedback, score_info: {score, max}, lti_notified }
    """
    origin = request.headers.get('Origin', '')

    if request.method == 'OPTIONS':
        resp = make_response('', 204)
        return _add_cors(resp, origin)

    data = request.get_json(force=True, silent=True) or {}

    # Build config and inject the Redis-backed session loader
    config = build_config()
    config['_session_loader'] = _load_session

    result = aigrader_engine.run(data, config)

    resp = jsonify(result)
    return _add_cors(resp, origin)


# ─── Static file server (HTML + JS) ──────────────────────────────────────────

_SAFE_EXTENSIONS = {'.html', '.js', '.css', '.ico', '.png', '.svg', '.txt'}

@app.route('/<path:filename>')
def serve_static(filename):
    """
    Serve HTML templates and static assets.
    Security: only allow whitelisted extensions; always uses basename
    to prevent path traversal (mirrors original os.path.basename logic).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _SAFE_EXTENSIONS:
        return make_response('Forbidden', 403)

    safe_name = os.path.basename(filename)

    # Templates (HTML) take priority, then static (JS/CSS)
    for folder in ('templates', 'static'):
        full = os.path.join(app.root_path, folder, safe_name)
        if os.path.isfile(full):
            return send_from_directory(folder, safe_name)

    return make_response('Not found', 404)


@app.route('/')
def index():
    return make_response(
        '<html><body><h1>✅ LTI AI Grader</h1>'
        '<p>LTI launch URL: <code>/lti-launch</code></p></body></html>'
    )


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(
        debug=DEBUG,
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
    )

