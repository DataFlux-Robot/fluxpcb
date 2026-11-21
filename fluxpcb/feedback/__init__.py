#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feedback — LLM+Rule Feedback Runtime (Component 8).

Bidirectional feedback: tune rules from outcomes, feed LLM history,
escalate method changes to Component 1.
"""
from .feedback_runtime import FeedbackRuntime

__all__ = ["FeedbackRuntime"]
