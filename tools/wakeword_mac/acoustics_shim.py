"""Drop-in stand-in for the `acoustics` package, installed as `acoustics/__init__.py`.

openWakeWord only calls acoustics.generator.noise(N, color). The real package is
unmaintained and no longer imports on current SciPy (sph_harm, interp2d removed),
so this reproduces just that function, using the same algorithm as acoustics 0.2.6.
"""

import types

import numpy as np
from numpy.fft import irfft


def _normalize(y):
    return y * np.sqrt(1.0 / (np.abs(y) ** 2.0).mean())


def _shaped(N, state, shape):
    state = np.random.RandomState() if state is None else state
    uneven = N % 2
    X = state.randn(N // 2 + 1 + uneven) + 1j * state.randn(N // 2 + 1 + uneven)
    y = irfft(shape(X, np.arange(len(X)))).real
    if uneven:
        y = y[:-1]
    return _normalize(y)


def white(N, state=None):
    state = np.random.RandomState() if state is None else state
    return state.randn(N)


def pink(N, state=None):
    return _shaped(N, state, lambda X, k: X / np.sqrt(k + 1.0))


def blue(N, state=None):
    return _shaped(N, state, lambda X, k: X * np.sqrt(k))


def brown(N, state=None):
    return _shaped(N, state, lambda X, k: X / (k + 1))


def violet(N, state=None):
    return _shaped(N, state, lambda X, k: X * k)


_noise_generators = {"white": white, "pink": pink, "blue": blue, "brown": brown, "violet": violet}


def noise(N, color="white", state=None):
    try:
        return _noise_generators[color](N, state)
    except KeyError:
        raise ValueError("Incorrect color.")


generator = types.SimpleNamespace(noise=noise, white=white, pink=pink, blue=blue,
                                  brown=brown, violet=violet)
