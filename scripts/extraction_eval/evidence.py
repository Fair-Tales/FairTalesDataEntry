"""Shared location for preserved evaluation evidence."""
import os
from pathlib import Path

from .tuning import DEFAULT_FREEZE_DIR


def evidence_dir():
    return Path(os.environ.get('FAIRTALES_EVIDENCE_DIR', DEFAULT_FREEZE_DIR / 'evidence'))
