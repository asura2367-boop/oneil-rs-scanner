"""Platform-neutral ephemeral app pair. No I/O, storage or credential discovery.

Redaction and pickle rejection are accidental-use guards, not memory isolation.
Python strings, debugger locals and crash dumps cannot be securely zeroized.
"""

class AppCredentials:
    """Ephemeral, redacted pair; no __dict__ or dataclass serialization surface."""
    __slots__ = ('app_key', 'app_secret')

    def __init__(self, app_key, app_secret):
        self.app_key = app_key
        self.app_secret = app_secret

    def __repr__(self):
        return 'AppCredentials(redacted=True)'

    def __eq__(self, other):
        return (type(other) is AppCredentials and self.app_key == other.app_key
                and self.app_secret == other.app_secret)

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_CREDENTIALS_SERIALIZATION_BLOCKED')
