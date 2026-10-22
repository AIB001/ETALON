"""Published producer contract identities survive Git text conversion unchanged."""
import ast
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = Path("src/etalon/research/contracts")


def pinned_contracts():
    modules = {"producer": "diagnostic-action", "pool": "fixed-pool",
               "pose": "pose-diagnostic", "preparation": "receptor-preparation"}
    for module, prefix in modules.items():
        tree = ast.parse((ROOT / "src/etalon/research" / f"{module}.py").read_text(encoding="utf8"))
        pins = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "SCHEMA_HASHES"
                        for target in node.targets))
        for kind, expected in pins.items():
            yield CONTRACTS / f"{prefix}-{kind}-v1.schema.json", expected


def test_all_published_contracts_match_the_existing_producer_byte_identities():
    for relative, expected in pinned_contracts():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative


def test_fresh_git_index_and_checkout_preserve_pins_even_with_autocrlf(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git checkout behavior requires Git")
    source, checkout = tmp_path / "source", tmp_path / "checkout"
    source.mkdir()
    checkout.mkdir()
    shutil.copyfile(ROOT / ".gitattributes", source / ".gitattributes")
    for relative, _ in pinned_contracts():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, source / relative)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.PIPE)
    git("init", "--quiet")
    git("config", "core.autocrlf", "true")
    git("add", "--", ".gitattributes", CONTRACTS.as_posix())
    git("checkout-index", "--all", "--prefix=" + checkout.as_posix() + "/")
    for relative, expected in pinned_contracts():
        assert hashlib.sha256(git("show", ":" + relative.as_posix())).hexdigest() == expected
        assert hashlib.sha256((checkout / relative).read_bytes()).hexdigest() == expected
