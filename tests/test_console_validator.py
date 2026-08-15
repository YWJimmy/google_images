from app.console_validator import html_contract, validate_base_url


def test_console_validator_accepts_loopback_only():
    assert validate_base_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    for invalid in ("https://127.0.0.1:8765", "http://0.0.0.0:8765", "http://example.com:8765"):
        try:
            validate_base_url(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid dashboard URL: {invalid}")


def test_console_html_contract_detects_duplicate_and_missing_ids():
    result = html_contract(
        '<div id="one"></div><div id="one"></div><script>$(\'one\');$(\'missing\')</script>'
    )
    assert result["duplicate_ids"] == ["one"]
    assert result["missing_references"] == ["missing"]
