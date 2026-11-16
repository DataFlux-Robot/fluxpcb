#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispatch — LLM+Rule Dispatch Runtime (Component 7).

Per-net routing params via LLM+rules with three-mode chain:
  1. LLM decision (if available)
  2. Rule-based fallback
  3. Baseline defaults

All decisions validated against rules. Offline mode is a first-class pass.
"""
from .dispatcher import DispatchRuntime

__all__ = ["DispatchRuntime"]
