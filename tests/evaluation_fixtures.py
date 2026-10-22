"""Shared offline fixtures for evaluation tests; nothing here calls a model."""

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# The editorial projection names this repository's readers; fixture projects hold none of them.
EDITORIAL = shutil.ignore_patterns('presentation.json')


def isolated_project(root):
    """Copy the study without admitted evaluation packages so tests never touch real data."""
    root = Path(root)
    for name in ['corpus', 'data', 'src', 'assets']:
        shutil.copytree(ROOT / name, root / name, ignore=EDITORIAL)
    shutil.rmtree(root / 'data/evaluations', ignore_errors=True)
    for name in ['LICENSE', 'LICENSE-CONTENT', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md']:
        shutil.copyfile(ROOT / name, root / name)
    return root


def live_shaped(evaluation_directory):
    """Reshape a synthetic scoreflow so admission can be tested without inference.

    Only receipts and the plan are rewritten, the way a passing live run would
    record them; answers, prompts and turn order stay exactly as produced.
    """
    evaluation_directory = Path(evaluation_directory)
    plan_path = evaluation_directory / 'plan.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    plan['dry_run'] = False
    plan_path.chmod(0o600)
    plan_path.write_bytes(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode() + b'\n')
    for path in evaluation_directory.glob('sessions/*/turns/*/receipt.json'):
        receipt = json.loads(path.read_text(encoding='utf-8'))
        receipt.update(dry_run=False, runtime_checks={'status': 'passed', 'phases': []})
        path.write_text(json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
    # Derived records name receipt hashes, so they are rebuilt from the reshaped receipts.
    derived = ['evaluation.json', 'status.json'] + (
        ['extraction.json'] if plan['extraction']['mode'] == 'session' else []
    )
    for name in derived:
        (evaluation_directory / name).unlink(missing_ok=True)
    return evaluation_directory


def admissible(project, evaluation_directory, evaluation_id):
    """Turn a dry-run scoreflow into a finished, admissible fixture without inference."""
    import scoring_pipeline

    live_shaped(evaluation_directory)
    scoring_pipeline.recover(project, evaluation_directory, evaluation_id=evaluation_id)
    return evaluation_directory
