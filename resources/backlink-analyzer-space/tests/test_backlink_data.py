from backlink_data import canonical_target_domain, extract_backlinks_from_payload, normalize_link_url


def test_canonical_target_domain_accepts_url_and_subdomain():
    assert canonical_target_domain("https://blog.Example.CO.UK/path") == "example.co.uk"


def test_normalize_link_url_removes_fragment_and_default_port():
    assert normalize_link_url("HTTPS://WWW.Example.com:443/a?q=1#section") == "https://example.com/a?q=1"


def test_extracts_only_destination_domain_links():
    payload = {
        "Envelope": {
            "Payload-Metadata": {
                "HTTP-Response-Metadata": {
                    "HTML-Metadata": {
                        "Links": [
                            {"path": "A@/href", "url": "/pricing", "text": "Pricing", "rel": "nofollow"},
                            {"path": "A@/href", "url": "https://docs.target.example/guide", "text": "Docs"},
                            {"path": "IMG@/src", "url": "https://other.example/image.png"},
                        ]
                    }
                }
            }
        }
    }
    rows = extract_backlinks_from_payload(
        payload=payload,
        source_url="https://publisher.example/article",
        target_domain="target.example",
        crawl="CC-MAIN-test",
        wat_source="test.wat.gz",
        capture_date="2026-08-02T00:00:00Z",
    )
    assert len(rows) == 1
    assert rows[0]["target_url"] == "https://docs.target.example/guide"
    assert rows[0]["source_domain"] == "publisher.example"
    assert rows[0]["is_nofollow"] == "false"
