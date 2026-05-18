"""A6: vault.py unit tests for scan/build_tree/search/resolve."""

import os
import sys

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import vault


@pytest.fixture
def sample_vault(tmp_path):
    root = tmp_path / 'vault'
    root.mkdir()
    (root / '.obsidian').mkdir()
    (root / '.obsidian' / 'workspace.json').write_text('{}', encoding='utf-8')
    (root / '폴더').mkdir()
    (root / '폴더' / '한글.md').write_text('# 한글 본문\nneedle 단어\n', encoding='utf-8')
    (root / 'top.md').write_text('# Top\nplain body\n', encoding='utf-8')
    (root / 'image.png').write_bytes(b'\x89PNG')
    return root


def test_resolve_vault_root_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        vault.resolve_vault_root(tmp_path / 'does-not-exist')


def test_resolve_vault_root_file_raises(tmp_path):
    f = tmp_path / 'a-file'
    f.write_text('hi')
    with pytest.raises(NotADirectoryError):
        vault.resolve_vault_root(f)


def test_scan_counts_only_markdown_files(sample_vault):
    result = vault.scan_vault(sample_vault)
    assert result['markdown_files'] == 2
    assert result['attachments'] >= 1


def test_scan_skips_dot_directories(sample_vault):
    # .obsidian/ must not contribute to scan totals
    result = vault.scan_vault(sample_vault)
    # only top.md and the nested Hangul page, not anything under .obsidian/
    rel_paths = {e.rel_path for e in vault.iter_markdown_files(sample_vault)}
    assert all(not r.startswith('.obsidian') for r in rel_paths)
    assert 'top.md' in rel_paths
    assert '폴더/한글.md' in rel_paths or '폴더\\한글.md' in rel_paths


def test_build_tree_structure(sample_vault):
    tree = vault.build_tree(sample_vault)
    # top.md is a leaf at root, and the nested folder is a dict.
    assert 'top.md' in tree
    assert tree['top.md'] is None
    assert isinstance(tree.get('폴더'), dict)
    assert '한글.md' in tree['폴더']


def test_search_finds_unicode_match(sample_vault):
    hits = vault.search_vault(sample_vault, 'needle')
    assert len(hits) == 1
    hit = hits[0]
    assert hit.line_number == 2
    assert 'needle' in hit.line


def test_search_case_insensitive(sample_vault):
    hits_lower = vault.search_vault(sample_vault, 'needle')
    hits_upper = vault.search_vault(sample_vault, 'NEEDLE')
    assert len(hits_lower) == len(hits_upper)


def test_search_handles_no_match(sample_vault):
    assert vault.search_vault(sample_vault, 'no-such-thing-anywhere') == []
