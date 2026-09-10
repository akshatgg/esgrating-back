# app/core/errors.py
class UserError(Exception):
    """An error whose message is safe and meant to be shown to the user verbatim."""
