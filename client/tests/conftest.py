"""Shared test setup. Some test modules stub libspeexdsp themselves and some don't; on a Mac without
Homebrew's speexdsp the ones that don't failed at import. Stub it once here, before any module loads."""
import sys
import types

try:
    import thinksmart_client.speex_aec  # noqa: F401  (needs libspeexdsp)
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)
