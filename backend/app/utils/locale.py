import json
import os
import threading
from flask import request, has_request_context

_thread_local = threading.local()

_locales_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'locales')

# Load language registry
with open(os.path.join(_locales_dir, 'languages.json'), 'r', encoding='utf-8') as f:
    _languages = json.load(f)

# Load translation files
_translations = {}
for filename in os.listdir(_locales_dir):
    if filename.endswith('.json') and filename != 'languages.json':
        locale_name = filename[:-5]
        with open(os.path.join(_locales_dir, filename), 'r', encoding='utf-8') as f:
            _translations[locale_name] = json.load(f)


def set_locale(locale: str):
    """Set locale for current thread. Call at the start of background threads."""
    _thread_local.locale = locale


def get_locale() -> str:
    if has_request_context():
        raw = request.headers.get('Accept-Language', 'en')
        return raw if raw in _languages and raw in _translations else 'en'
    raw = getattr(_thread_local, 'locale', 'en')
    return raw if raw in _languages and raw in _translations else 'en'


def t(key: str, **kwargs) -> str:
    locale = get_locale()
    messages = _translations.get(locale, _translations.get('en', {}))

    value = messages
    for part in key.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = None
            break

    if value is None:
        value = _translations.get('en', {})
        for part in key.split('.'):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break

    if value is None:
        return key

    if kwargs:
        for k, v in kwargs.items():
            value = value.replace(f'{{{k}}}', str(v))

    return value


def get_language_instruction() -> str:
    # Product/runtime output is intentionally locked to English for now.
    # The original project had Chinese prompt paths that could leak Mandarin
    # into generated reports even when the UI was set to English.
    return 'Please respond in English only. Do not use Mandarin or Chinese characters.'
