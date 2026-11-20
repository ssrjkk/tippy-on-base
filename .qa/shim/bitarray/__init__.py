"""Local stand-in for the compiled `bitarray._bitarray`, which Smart App Control
refuses to load on this host. Enough for import-time use only: the point is to let
pytest collect the suite, not to sign anything."""

BITARRAY_VERSION = "0.0-shim"


class bitarray(list):
    def __init__(self, init=(), endian="big"):
        super().__init__(bool(b) for b in init)

    def tobytes(self):
        return bytes()

    @classmethod
    def frombytes(cls, _b):
        return cls()

    def decode(self, *_a, **_k):
        return ""

    def encode(self, *_a, **_k):
        return self

    def to01(self):
        return "".join("1" if b else "0" for b in self)


class decodetree(dict):
    def make_tree(cls, *_a, **_k):
        return cls()


decodeiterator = iter
_bitarray_reconstructor = None
get_default_endian = lambda: "big"  # noqa: E731
bits2bytes = lambda n: (n + 7) // 8  # noqa: E731
_sysinfo = lambda: {}
