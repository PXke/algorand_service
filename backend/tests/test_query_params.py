"""Query-param normalization for framework-dependent value shapes."""

from app.core.query_params import query_param


def test_query_param_flattens_list_values() -> None:
    """Normalizes list/tuple/None query-param shapes to a single trimmed string."""
    assert query_param(["fa", "en"]) == "fa"
    assert query_param(("ar",)) == "ar"
    assert query_param("  fr  ") == "fr"
    assert query_param(None) == ""
    assert query_param([]) == ""
