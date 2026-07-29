"""URL/domain normalization shared by catalog loading, OpenPageRank, and availability checks."""
from __future__ import annotations

from urllib.parse import urlparse

import tldextract

# include_psl_private_domains=True matters here: many catalog sites are hosted on
# multi-tenant blogging platforms (blogspot.com, wordpress.com, ...). Without this,
# tldextract treats "blogspot.com" itself as the registrable domain, so e.g.
# "mythicalbooks.blogspot.com" and "unrelated-blog.blogspot.com" — two completely
# different guest-post sites — would collapse into a single catalog entry. With it,
# each user's blog is correctly treated as its own registrable domain, while ordinary
# "www.example.com" vs "example.com" variants still collapse together as expected.
_EXTRACTOR = tldextract.TLDExtract(include_psl_private_domains=True)


def registrable_domain(url: str) -> str:
    """Return the registrable domain (e.g. 'blog.example.co.uk' -> 'example.co.uk',
    'mythicalbooks.blogspot.com' -> 'mythicalbooks.blogspot.com')."""
    ext = _EXTRACTOR(url)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return (urlparse(url).netloc or url).lower()
