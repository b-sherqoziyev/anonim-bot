"""
Utility functions module.
Contains helper functions for token generation, datetime formatting, and other utilities.
"""
import string
import random


def generate_token(length=8):
    """Generate a random alphanumeric token of specified length."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))




