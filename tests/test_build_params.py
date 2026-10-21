from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.linkedin_jobs import build_params


def test_multiword_keywords_are_quoted():
    params = build_params(SearchFilters(keywords="desenvolvedor fullstack"))
    assert params["keywords"] == '"desenvolvedor fullstack"'


def test_single_keyword_not_quoted():
    params = build_params(SearchFilters(keywords="react"))
    assert params["keywords"] == "react"


def test_jr_expanded_to_junior():
    params = build_params(SearchFilters(keywords="dev jr"))
    assert "junior" in params["keywords"]
    # não deve alterar palavras contendo "jr"
    params2 = build_params(SearchFilters(keywords="jrx"))
    assert params2["keywords"] == "jrx"


def test_location_appends_brasil():
    params = build_params(SearchFilters(keywords="x", location="São Paulo"))
    assert params["location"] == "São Paulo, Brasil"
    params2 = build_params(SearchFilters(keywords="x", location="Brasil"))
    assert params2["location"] == "Brasil"


def test_time_range_and_sort():
    params = build_params(SearchFilters(keywords="x", time_range="r24h"))
    assert params["f_TPR"] == "r86400"
    assert params["sortBy"] == "DD"
    # desconhecido cai no default 7 dias
    params2 = build_params(SearchFilters(keywords="x", time_range="zzz"))
    assert params2["f_TPR"] == "r604800"


def test_remote_filter():
    params = build_params(SearchFilters(keywords="x", remote="remote"))
    assert params["f_WT"] == "2"
    params2 = build_params(SearchFilters(keywords="x"))
    assert "f_WT" not in params2


def test_geo_id_for_brasil():
    params = build_params(SearchFilters(keywords="x", location="Brasil"))
    assert params["geoId"] == "106057199"
    params2 = build_params(SearchFilters(keywords="x", location="São Paulo"))
    assert "geoId" not in params2


def test_pagination_start():
    assert build_params(SearchFilters(keywords="x"), start=50)["start"] == "50"
