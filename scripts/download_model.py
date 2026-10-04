"""Explicit command-line model download, using the same verified cache as the GUI."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from transcription.models import CATALOG, ModelManager
from utils.paths import Paths
from utils.process import Cancellation

parser = argparse.ArgumentParser()
parser.add_argument('model', choices=list(CATALOG))
args = parser.parse_args()
manager = ModelManager(Paths().models)
cancel = Cancellation()
try:
    with manager.lease(args.model):
        target = manager.ensure(args.model, cancel, lambda e: print(f"\r{e['stage']} {e['fraction']:.0%}", end='', flush=True))
    print('\n' + str(target))
except KeyboardInterrupt:
    cancel.cancel()
    print('\nCancelled')
    sys.exit(130)
