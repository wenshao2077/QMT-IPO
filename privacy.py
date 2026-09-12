"""Redact secrets at output boundaries, without hiding stable diagnostic codes."""
import re


def redact(value, config=None):
    text = str(value)
    if config:
        account = config.get('account_id')
        if account:
            text = text.replace(str(account), '[ACCOUNT]')
    text = re.sub(r'https?://[^\s"<>]+', '[URL]', text, flags=re.I)
    text = re.sub(r'(?i)(key|token|password|secret|webhook)([\s"\x27]*[:=][\s"\x27]*)([^\s,;}"\x27]+)', r'\1\2[REDACTED]', text)
    return text
