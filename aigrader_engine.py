#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aigrader_engine.py
Pure grading logic – no CGI, no filesystem I/O, no stdout writes.

This is a direct port of aigrader.py with three changes:
  1. run() takes (data: dict, config: dict) and returns a dict
     instead of reading from CGI stdin and writing to CGI stdout.
  2. Session is loaded via config['_session_loader'](token)
     instead of reading a JSON file from /var/secure/lti_sessions/.
  3. No cgitb / no sys.tracebacklimit (error handling is Flask's job).

All security logic is preserved unchanged:
  - is_safe_url(): HTTPS + domain whitelist before grade passback
  - send_grade_to_lti(): OAuth 1.0a HMAC-SHA1 signing (identical)
  - extract_flexible_grade(): regex on grade_identifier (identical)
  - Empty / unchanged submission detection (identical)
"""

import os
import re
import json
import time
import uuid
import hashlib
import hmac
import base64
import logging
import urllib.request
import urllib.parse
import urllib.error
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─── URL safety check ────────────────────────────────────────────────────────

def is_safe_url(url: str, allowed_domains_str: str, base_url: str = '') -> tuple:
    """
    Returns (True, final_url) if url is HTTPS and matches an allowed domain.
    Returns (False, reason_str) otherwise.
    Identical to the original implementation.
    """
    try:
        if not url:
            return False, "Empty URL"
        if url.startswith('/'):
            url = base_url.rstrip('/') + url
        parsed = urlparse(url)
        if parsed.scheme != 'https':
            return False, "Only HTTPS connections"
        allowed_list = [d.strip().lower() for d in allowed_domains_str.split(',')]
        domain = parsed.netloc.lower()
        if any(domain == d or domain.endswith('.' + d) for d in allowed_list):
            return True, url
        return False, f"Non-authorised domain: {domain}"
    except Exception as exc:
        return False, f"URL processing error: {exc}"


# ─── Grade extraction ────────────────────────────────────────────────────────

def extract_flexible_grade(text: str, grade_identifier: str):
    """
    Searches for GRADE_IDENTIFIER: X/Y in the LLM output.
    Returns (score_float, maximum_float) or (None, None).
    Identical to the original implementation.
    """
    try:
        pattern = rf"{re.escape(grade_identifier)}[:\s]*([\d.]+)\s*/\s*([\d.]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1)), float(match.group(2))
    except Exception:
        pass
    return None, None


# ─── LTI 1.1 grade passback ──────────────────────────────────────────────────

def send_grade_to_lti(
    outcome_url: str,
    result_sourcedid: str,
    consumer_key: str,
    score_normalized: float,
    config: dict,
) -> bool:
    """
    Send grade back to LMS via LTI 1.1 Outcomes Service.
    Uses OAuth 1.0a HMAC-SHA1 – identical to the original implementation.
    """
    try:
        safe, final_url = is_safe_url(
            outcome_url,
            config.get('LTI_ALLOWED_DOMAINS', ''),
            config.get('BASE_URL', ''),
        )
        if not safe:
            log.warning("Grade passback blocked – URL not safe: %s", final_url)
            return False

        secret = config.get('lti_consumer_secrets', {}).get(consumer_key)
        if not secret:
            log.warning("Grade passback blocked – no secret for key: %s", consumer_key)
            return False

        xml_body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<imsx_POXEnvelopeRequest '
            'xmlns="http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0">'
            '<imsx_POXHeader><imsx_POXRequestHeaderInfo>'
            '<imsx_version>V1.0</imsx_version>'
            f'<imsx_messageIdentifier>{uuid.uuid4()}</imsx_messageIdentifier>'
            '</imsx_POXRequestHeaderInfo></imsx_POXHeader>'
            '<imsx_POXBody><replaceResultRequest><resultRecord>'
            f'<sourcedGUID><sourcedId>{result_sourcedid}</sourcedId></sourcedGUID>'
            '<result><resultScore><language>en</language>'
            f'<textString>{float(score_normalized):.4f}</textString>'
            '</resultScore></result>'
            '</resultRecord></replaceResultRequest></imsx_POXBody>'
            '</imsx_POXEnvelopeRequest>'
        )

        body_hash = base64.b64encode(
            hashlib.sha1(xml_body.encode('utf-8')).digest()
        ).decode('utf-8')

        oauth_params = {
            'oauth_body_hash':        body_hash,
            'oauth_consumer_key':     consumer_key,
            'oauth_nonce':            uuid.uuid4().hex,
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_timestamp':        str(int(time.time())),
            'oauth_version':          '1.0',
        }

        encoded_url   = urllib.parse.quote(final_url, safe='')
        sorted_params = '&'.join(
            f"{urllib.parse.quote(k)}={urllib.parse.quote(v)}"
            for k, v in sorted(oauth_params.items())
        )
        base_string  = f"POST&{encoded_url}&{urllib.parse.quote(sorted_params)}"
        signing_key  = f"{urllib.parse.quote(secret)}&".encode('utf-8')
        signature    = base64.b64encode(
            hmac.new(signing_key, base_string.encode('utf-8'), hashlib.sha1).digest()
        ).decode('utf-8')

        oauth_params['oauth_signature'] = signature
        auth_header = 'OAuth ' + ', '.join(
            f'{k}="{urllib.parse.quote(v)}"' for k, v in oauth_params.items()
        )

        req = urllib.request.Request(
            final_url,
            data=xml_body.encode('utf-8'),
            headers={
                'Content-Type': 'application/xml',
                'Authorization': auth_header,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            success = resp.getcode() == 200
            if not success:
                log.warning("LMS grade passback returned HTTP %s", resp.getcode())
            return success

    except Exception as exc:
        log.error("send_grade_to_lti error: %s", exc)
        return False


# ─── AI API call ─────────────────────────────────────────────────────────────

def call_ai_api(student_input: str, config: dict) -> dict:
    """
    Call Gemini (Google) or OpenAI-compatible API.
    Identical to the original implementation.
    """
    provider = config.get('provider', 'openai').lower()
    try:
        if provider == 'openai':
            url = config.get('api_url') or 'https://api.openai.com'
            url = url.rstrip('/')
            if '/chat/completions' not in url:
                url += '/v1/chat/completions'
            headers = {
                'Content-Type':  'application/json',
                'Authorization': f"Bearer {config['api_key']}",
            }
            body = {
                'model': config['model_name'],
                'messages': [
                    {'role': 'system', 'content': config['system_instructions']},
                    {'role': 'user',   'content': f"Student Text:\n{student_input}"},
                ],
                'temperature': 0.2,
            }
        else:  # google / gemini
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{config['model_name']}:generateContent?key={config['api_key']}"
            )
            headers = {'Content-Type': 'application/json'}
            body = {
                'contents': [{
                    'parts': [{
                        'text': f"{config['system_instructions']}\n\nStudent Text:\n{student_input}"
                    }]
                }]
            }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode('utf-8'),
            headers=headers,
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode('utf-8'))

        if provider == 'openai':
            feedback = res['choices'][0]['message']['content']
        else:
            feedback = res['candidates'][0]['content']['parts'][0]['text']

        return {'success': True, 'feedback': feedback}

    except Exception as exc:
        log.error("AI API call failed: %s", exc)
        return {'success': False, 'error': str(exc)}


# ─── Main run function ───────────────────────────────────────────────────────

def run(data: dict, config: dict) -> dict:
    """
    Main grading function.

    Args:
        data:   dict parsed from the JSON POST body sent by aigrader.js
                  { studentInput, defaultValue, session_token, emptyErrorMsg }
        config: grader configuration dict (from grader_config.build_config())
                  must include '_session_loader' callable

    Returns:
        dict  { success, feedback, score_info: {score, max}, lti_notified }
    """
    student_input  = data.get('studentInput',  '').strip()
    default_value  = data.get('defaultValue',  '').strip()
    session_token  = data.get('session_token') or data.get('token', '')
    empty_error    = data.get('emptyErrorMsg', 'Error: Empty or unchanged submission.')

    # Reject empty / unchanged submissions (identical to original)
    clean_input   = re.sub(r'\s+', '', student_input)
    clean_default = re.sub(r'\s+', '', default_value)
    if not student_input or clean_input == clean_default:
        return {
            'success':    True,
            'feedback':   empty_error,
            'score_info': {'score': 0, 'max': 5},
            'lti_notified': False,
        }

    # Call AI
    result = call_ai_api(student_input, config)

    if not result.get('success'):
        return {'success': False, 'error': result.get('error', 'Unknown AI error')}

    feedback = result['feedback']
    score, maximum = extract_flexible_grade(feedback, config['grade_identifier'])

    # Grade passback to LMS
    grade_sent = False
    if session_token and score is not None:
        session_loader = config.get('_session_loader')
        if session_loader:
            session_data = session_loader(session_token)
            if session_data:
                lti_params = session_data.get('lti_params', {})
                if config.get('send_grade_to_lms') and lti_params:
                    grade_sent = send_grade_to_lti(
                        lti_params.get('lis_outcome_service_url'),
                        lti_params.get('lis_result_sourcedid'),
                        lti_params.get('oauth_consumer_key'),
                        score / maximum if maximum and maximum > 0 else 0,
                        config,
                    )

    return {
        'success':      True,
        'feedback':     feedback,
        'score_info':   {'score': score, 'max': maximum},
        'lti_notified': grade_sent,
    }
