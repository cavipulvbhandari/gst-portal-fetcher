from __future__ import annotations

from gstfetch.client import GstClient, looks_like_waf_block
from gstfetch.endpoints import ResourceSpec

# The actual F5 firewall page the portal returns (with a 200 status).
WAF_PAGE = (
    "<html><head><title>Request Rejected</title></head><body>"
    "The requested URL was rejected. Please consult with your administrator.<br><br>"
    "Your support ID is: 17607532191030279631<br><br>"
    "<a href='javascript:history.back();'>[Go Back]</a></body></html>"
)


def test_looks_like_waf_block_detects_firewall_page() -> None:
    assert looks_like_waf_block(WAF_PAGE) is True


def test_looks_like_waf_block_ignores_real_payloads() -> None:
    assert looks_like_waf_block('{"filingStatus": []}') is False
    assert looks_like_waf_block("<html>Some other error</html>") is False
    assert looks_like_waf_block("") is False
    # Needs BOTH markers — a stray "support id" mention alone is not a block.
    assert looks_like_waf_block("contact support id desk") is False


def test_render_path_substitutes_and_encodes() -> None:
    spec = ResourceSpec(
        name="filing_history",
        scope="fy",
        method="GET",
        path="/services/api/returns/dashboard",
        params={"gstin": "{gstin}", "fy": "{fy}"},
    )
    rendered = GstClient.render_path(spec, {"gstin": "27AAACX1234F1Z5", "fy": "2019-20"})
    assert rendered.startswith("/services/api/returns/dashboard?")
    assert "gstin=27AAACX1234F1Z5" in rendered
    assert "fy=2019-20" in rendered


def test_render_path_without_params() -> None:
    spec = ResourceSpec(
        name="profile", scope="once", method="GET", path="/services/api/get/gstndata"
    )
    assert GstClient.render_path(spec, {}) == "/services/api/get/gstndata"
