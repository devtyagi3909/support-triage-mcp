"""
Telemetry and Observability hooks for Langfuse.
Provides a transparent no-op fallback when LANGFUSE_PUBLIC_KEY is not configured.
"""

import os
from functools import wraps

_HAS_LANGFUSE = False
try:
    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        from langfuse.decorators import observe as _langfuse_observe
        _HAS_LANGFUSE = True
except ImportError:
    _HAS_LANGFUSE = False


def observe(*args, **kwargs):
    """
    Decorator for tracing workflow execution in Langfuse.
    Falls back gracefully to a standard function execution wrapper when Langfuse is not enabled.
    """
    if _HAS_LANGFUSE:
        return _langfuse_observe(*args, **kwargs)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            return fn(*a, **kw)
        return wrapper

    if len(args) == 1 and callable(args[0]) and not kwargs:
        return decorator(args[0])
    return decorator
