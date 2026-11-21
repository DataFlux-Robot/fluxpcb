#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strategy_store — Strategy Store (Component 1).

Persists reusable routing strategies keyed by board features.
Enables generalization to any board.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional


class StrategyStore:
    """Strategy store — persists routing strategies.

    Keys on board features (family, ball_count, layers) so strategies
    learned on one board transfer to similar boards.
    """

    def __init__(self, store_dir: str = ".strategy_store"):
        """Initialize strategy store.

        Args:
            store_dir: Directory to store strategies
        """
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    def save(self, strategy: dict, board_features: dict) -> str:
        """Save a strategy.

        Args:
            strategy: Strategy parameters
            board_features: Board features for keying

        Returns:
            Strategy ID
        """
        key = self._make_key(board_features)
        path = os.path.join(self.store_dir, f"{key}.json")

        data = {
            'strategy': strategy,
            'board_features': board_features,
            'key': key,
        }

        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

        return key

    def load_for(self, board_features: dict) -> Optional[dict]:
        """Load strategy for a board.

        Args:
            board_features: Board features for keying

        Returns:
            Strategy dict or None
        """
        key = self._make_key(board_features)
        path = os.path.join(self.store_dir, f"{key}.json")

        if not os.path.exists(path):
            # Try similar boards
            return self._find_similar(board_features)

        with open(path) as f:
            data = json.load(f)

        return data.get('strategy')

    def _make_key(self, features: dict) -> str:
        """Make a key from board features."""
        key_str = f"{features.get('family', 'unknown')}_{features.get('layers', 2)}_{features.get('ball_count', 0)}"
        return hashlib.sha256(key_str.encode()).hexdigest()[:12]

    def _find_similar(self, features: dict) -> Optional[dict]:
        """Find a strategy for a similar board."""
        for f in os.listdir(self.store_dir):
            if not f.endswith('.json'):
                continue
            path = os.path.join(self.store_dir, f)
            try:
                with open(path) as fp:
                    data = json.load(fp)
                stored = data.get('board_features', {})
                if self._is_similar(features, stored):
                    return data.get('strategy')
            except Exception:
                continue
        return None

    def _is_similar(self, f1: dict, f2: dict) -> bool:
        """Check if two board feature sets are similar."""
        return (f1.get('layers') == f2.get('layers') and
                f1.get('family') == f2.get('family'))

    def list_strategies(self) -> list[dict]:
        """List all stored strategies."""
        strategies = []
        for f in os.listdir(self.store_dir):
            if f.endswith('.json'):
                path = os.path.join(self.store_dir, f)
                try:
                    with open(path) as fp:
                        strategies.append(json.load(fp))
                except Exception:
                    continue
        return strategies
