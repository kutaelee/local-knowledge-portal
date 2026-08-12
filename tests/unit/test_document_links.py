from lkp_indexer.document_links import extract_document_links


def test_markdown_link_extraction_skips_external_traversal_and_code_fences():
    content = """# Links
[[Runbooks/Recovery|복구]]
[ADR](../escape.md)
[local](architecture/system.md#queue)
[external](https://example.com/docs)
```md
[[ignored]]
```
"""

    links = extract_document_links(content)

    assert [(item.raw_target, item.link_type, item.source_line) for item in links] == [
        ("Runbooks/Recovery", "wikilink", 2),
        ("architecture/system.md", "markdown", 4),
    ]


def test_wikilink_heading_and_unique_lines_are_preserved():
    links = extract_document_links("[[Worker#Lease]]\n[[Worker]]")
    assert [item.raw_target for item in links] == ["Worker", "Worker"]
    assert [item.source_line for item in links] == [1, 2]
