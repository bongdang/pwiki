import os
import sys

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import app as pwiki_app
import config
import rendering


def test_heading_and_paragraph():
    html = pwiki_app.markdown_to_html("# Title\n\nHello *world*.")
    assert "<h1>Title</h1>" in html
    assert "<em>world</em>" in html


def test_fenced_code_block():
    html = pwiki_app.markdown_to_html("```python\nprint('hi')\n```")
    assert '<div class="code-block">' in html
    assert '<span class="code-lang">python</span>' in html
    assert '<button type="button" class="code-copy" title="Copy code">copy</button>' in html
    assert '<pre><code class="language-python">' in html
    assert "print('hi')" in html


def test_gfm_table():
    src = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    html = pwiki_app.markdown_to_html(src)
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<td>1</td>" in html


def test_task_list():
    html = pwiki_app.markdown_to_html("- [ ] todo\n- [x] done\n")
    assert 'task-list-item' in html
    assert 'checked="checked"' in html


def test_frontmatter_is_consumed():
    src = '---\ntitle: Hello\n---\n\n# Body\n'
    html = pwiki_app.markdown_to_html(src)
    # frontmatter line content should not leak into rendered HTML
    assert 'title: Hello' not in html
    assert '<h1>Body</h1>' in html


def test_raw_html_is_blocked():
    src = '<script>alert(1)</script>\n\nplain text\n'
    html = pwiki_app.markdown_to_html(src)
    # html=False causes raw HTML to be escaped, not passed through
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_obsidian_wikilink_renders_page_link(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))
    pwiki_app.write_page('Notes/Alpha', '# Alpha')

    html = pwiki_app.markdown_to_html('See [[Notes/Alpha|Alpha note]] and [[Missing]].')

    assert '<a href="/Notes/Alpha">Alpha note</a>' in html
    assert '<a href="/Missing">Missing?</a>' in html


def test_obsidian_wikilink_resolves_unique_basename_like_obsidian(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))
    pwiki_app.write_page('기록들/개발관련/Git_간략_사용', '# Git')
    pwiki_app.write_page('기록들/개발관련/AI/토큰_절약', '# Tokens')

    html = pwiki_app.markdown_to_html('[[git 간략 사용]] [[토큰 절약]]')

    assert 'href="/%EA%B8%B0%EB%A1%9D%EB%93%A4/%EA%B0%9C%EB%B0%9C%EA%B4%80%EB%A0%A8/Git_%EA%B0%84%EB%9E%B5_%EC%82%AC%EC%9A%A9">git 간략 사용</a>' in html
    assert 'href="/%EA%B8%B0%EB%A1%9D%EB%93%A4/%EA%B0%9C%EB%B0%9C%EA%B4%80%EB%A0%A8/AI/%ED%86%A0%ED%81%B0_%EC%A0%88%EC%95%BD">토큰 절약</a>' in html


def test_obsidian_wikilink_resolves_slash_as_basename_text(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))
    pwiki_app.write_page('기록들/개발관련/UI_UX_관련_정보_모음', '# UX')

    html = pwiki_app.markdown_to_html('[[UI/UX 관련 정보 모음]]')

    assert 'href="/%EA%B8%B0%EB%A1%9D%EB%93%A4/%EA%B0%9C%EB%B0%9C%EA%B4%80%EB%A0%A8/UI_UX_%EA%B4%80%EB%A0%A8_%EC%A0%95%EB%B3%B4_%EB%AA%A8%EC%9D%8C">UX 관련 정보 모음</a>' in html


def test_obsidian_wikilink_keeps_question_mark_for_ambiguous_basename(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))
    pwiki_app.write_page('A/Same_Name', '# A')
    pwiki_app.write_page('B/Same_Name', '# B')

    html = pwiki_app.markdown_to_html('[[Same Name]] [[A/Same Name]]')

    assert '<a href="/Same%20Name">Same Name?</a>' in html
    assert '<a href="/A/Same_Name">Same Name</a>' in html


def test_obsidian_embed_renders_attachment_image(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))

    html = pwiki_app.markdown_to_html('![[assets/pic.png]]')

    assert '<img src="/attach/assets/pic.png" alt="pic.png" />' in html


def test_obsidian_embed_image_width(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))

    html = pwiki_app.markdown_to_html('![[assets/pic.png|640]]')

    assert '<img src="/attach/assets/pic.png" alt="pic.png" width="640">' in html
    assert 'height=' not in html


def test_obsidian_embed_image_width_and_height(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))

    html = pwiki_app.markdown_to_html('![[assets/pic.png|640x480]]')

    assert '<img src="/attach/assets/pic.png" alt="pic.png" width="640" height="480">' in html


def test_obsidian_embed_non_numeric_pipe_stays_caption(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))

    html = pwiki_app.markdown_to_html('![[assets/pic.png|My caption]]')

    # A non-dimension pipe value remains the alt text (no width injected).
    assert 'alt="My caption"' in html
    assert 'width=' not in html


def test_obsidian_embed_sized_image_percent_encodes_src(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))

    html = pwiki_app.markdown_to_html('![[첨부 파일.png|320]]')

    assert 'src="/attach/%EC%B2%A8%EB%B6%80%20%ED%8C%8C%EC%9D%BC.png"' in html
    assert 'width="320"' in html


def test_obsidian_links_percent_encode_href(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))
    pwiki_app.write_page('기록들/한글 페이지', '# Title')

    html = pwiki_app.markdown_to_html('[[기록들/한글 페이지]] ![[첨부 파일.png]]')

    assert 'href="/%EA%B8%B0%EB%A1%9D%EB%93%A4/%ED%95%9C%EA%B8%80%20%ED%8E%98%EC%9D%B4%EC%A7%80"' in html
    assert 'src="/attach/%EC%B2%A8%EB%B6%80%20%ED%8C%8C%EC%9D%BC.png"' in html


def test_obsidian_links_are_ignored_inside_fenced_code(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))

    html = pwiki_app.markdown_to_html("```text\n[[NotALink]]\n```\n[[RealLink]]")

    assert "[[NotALink]]" in html
    assert '<a href="/RealLink">RealLink?</a>' in html


def test_obsidian_highlight_renders_mark():
    html = pwiki_app.markdown_to_html("This is ==important==.")
    assert "This is <mark>important</mark>." in html


def test_obsidian_callout_marker_renders_readable_blockquote():
    html = pwiki_app.markdown_to_html("> [!warning] Check this\n> body")
    assert "<blockquote>" in html
    assert "<strong>Warning: Check this</strong>" in html
    assert "body" in html


def test_obsidian_tag_links_to_search():
    html = pwiki_app.markdown_to_html("Tags: #project/wiki and #일상")
    assert '<a href="/?action=search&amp;q=%23project%2Fwiki" class="md-tag">#project/wiki</a>' in html
    assert '<a href="/?action=search&amp;q=%23%EC%9D%BC%EC%83%81" class="md-tag">#일상</a>' in html


def test_obsidian_highlight_callout_tag_ignored_inside_fenced_code():
    html = pwiki_app.markdown_to_html("```text\n==raw==\n> [!note]\n#tag\n```\n")
    assert "==raw==" in html
    assert "[!note]" in html
    assert "#tag" in html
    assert "<mark>" not in html


def test_render_page_uses_markdown_renderer():
    html = pwiki_app.render_page("# md heading")
    assert "<h1>md heading</h1>" in html


def test_section_split_uses_markdown_headings():
    text = "preamble\n# One\nA\n## Two\nB\n# Three\nC\n"
    parts = pwiki_app._split_sections(text)
    # preamble + 3 headings = 4 parts
    assert len(parts) == 4
    assert parts[0].startswith("preamble")
    assert parts[1].startswith("# One")
    assert parts[2].startswith("## Two")
    assert parts[3].startswith("# Three")


def test_section_split_ignores_markdown_headings_inside_fenced_code():
    text = (
        "# One\n"
        "before\n"
        "```python\n"
        "# not a heading\n"
        "print('hi')\n"
        "```\n"
        "## Two\n"
        "after\n"
    )
    parts = pwiki_app._split_sections(text)
    assert len(parts) == 3
    assert "# not a heading" in parts[1]
    assert parts[2].startswith("## Two")


def test_obsidian_link_rejects_javascript_scheme():
    html = pwiki_app.markdown_to_html("[[javascript:alert(1)]]")
    assert 'javascript' not in html.lower() or '<a' not in html
    assert 'href=' not in html or 'href=""' in html or 'href="' + (config.URL_PREFIX or '') + '/' not in html.replace(
        'href="' + (config.URL_PREFIX or '') + '/javascript', 'BLOCKED'
    )


def test_obsidian_embed_rejects_data_scheme():
    html = pwiki_app.markdown_to_html("![[data:text/html,<x>]]")
    assert 'data:' not in html
    assert '<x>' not in html


def test_obsidian_callout_title_escapes_html():
    html = pwiki_app.markdown_to_html("> [!warning] <b>danger</b>\n> body\n")
    assert '<b>danger</b>' not in html  # markdown-it html=False already escapes, but title shouldn't survive raw


def test_md_tag_class_not_applied_to_unrelated_search_link():
    # A regular search link (not a tag) must NOT get the md-tag class.
    html = pwiki_app.markdown_to_html("[search](?action=search&q=plain)")
    assert 'md-tag' not in html


def test_md_tag_class_applied_to_real_tag_link():
    html = pwiki_app.markdown_to_html("#hello world")
    assert 'md-tag' in html


def test_normalize_obsidian_lookup_key_handles_nfd():
    import unicodedata
    nfc = '기록'
    nfd = unicodedata.normalize('NFD', nfc)
    assert pwiki_app._normalize_obsidian_lookup_key(nfd) == pwiki_app._normalize_obsidian_lookup_key(nfc)


def test_resolve_obsidian_page_caches_normalized_keys_per_request(monkeypatch):
    pages = ['Notes/Alpha_Page', 'Notes/Beta_Page']
    calls = 0
    original = rendering._normalize_obsidian_lookup_key

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(rendering, 'get_all_pages', lambda: pages)
    monkeypatch.setattr(rendering, '_normalize_obsidian_lookup_key', counted)

    with pwiki_app.app.test_request_context('/'):
        assert rendering._resolve_obsidian_page('Alpha Page') == 'Notes/Alpha_Page'
        assert rendering._resolve_obsidian_page('Beta Page') == 'Notes/Beta_Page'

    # First lookup normalizes the query plus both full paths and basenames.
    # Second lookup should only normalize the new query and reuse g._normalized_keys.
    assert calls == 6
