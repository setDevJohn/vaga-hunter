from pathlib import Path

from vaga_hunter.config import load_config, save_search_params


def test_save_search_params_roundtrip(tmp_path: Path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        "# meu comentário\n[search]\nqueries = [\"a\"]\nlocation = \"Brasil\"\n"
        "\n[scoring]\nthreshold = 60\n\n[http]\nmin_delay = 1.5\n")
    cfg = load_config(toml)
    cfg.search.queries = ["python", "react"]
    cfg.search.time_range = "r3d"
    cfg.scoring.threshold = 75.0
    assert save_search_params(cfg) is True

    text = toml.read_text()
    assert "# meu comentário" in text          # comentários preservados
    assert "min_delay = 1.5" in text           # outras seções intactas
    cfg2 = load_config(toml)
    assert cfg2.search.queries == ["python", "react"]
    assert cfg2.search.time_range == "r3d"
    assert cfg2.scoring.threshold == 75.0


def test_save_without_file_returns_false():
    from vaga_hunter.config import Config
    assert save_search_params(Config()) is False
