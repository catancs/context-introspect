#!/usr/bin/env python3
"""context-introspect: audit Claude Code config cost vs. real usage."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

WINDOW_DAYS = 30
DISABLED_DIRNAME = ".context-introspect-disabled"

Item = dict


# ---------------------------------------------------------------------------
# Task 2: estimate_tokens / parse_ts
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough heuristic: ~4 chars per token. Labelled 'estimated' everywhere."""
    if not text:
        return 0
    return len(text) // 4


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
