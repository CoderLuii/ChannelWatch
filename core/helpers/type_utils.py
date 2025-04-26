"""
Type safety utilities for handling optional values and type casting.
"""
from typing import TypeVar, Optional, Any, Dict, List, Union, Tuple, cast, overload

# TYPE VARIABLES
T = TypeVar('T')

# TYPE SAFETY
def ensure_str(value: Optional[str]) -> str:
    """Converts optional string to non-optional string with empty string fallback."""
    return value if value is not None else ""

def ensure_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Converts optional dictionary to non-optional dictionary with empty dict fallback."""
    return value if value is not None else {}

def cast_optional(value: Optional[T]) -> T:
    """Performs type casting from Optional[T] to T for static type checking."""
    return cast(T, value) 