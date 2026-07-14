"""Named dataset hooks (preprocess/postprocess) referenced from YAML."""
from __future__ import annotations

from typing import Callable

import pandas as pd

HOOKS: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {}


def register(name: str):
    def deco(fn):
        HOOKS[name] = fn
        return fn
    return deco


def get_hook(name: str) -> Callable[[pd.DataFrame], pd.DataFrame]:
    if name not in HOOKS:
        raise KeyError(f"unknown hook '{name}'; registered: {sorted(HOOKS)}")
    return HOOKS[name]
