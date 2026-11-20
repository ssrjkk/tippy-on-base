"""Import-time stand-in for `bitarray.util` (see `bitarray/__init__.py`)."""


def __getattr__(name):
    def _unavailable(*_args, **_kwargs):
        raise NotImplementedError(f"bitarray.util.{name} is a shim on this host")
    return _unavailable
