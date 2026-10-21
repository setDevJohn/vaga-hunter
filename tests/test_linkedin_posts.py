from vaga_hunter.sources.linkedin_posts import extract_jobs_from_posts


def make_post(urn_id: str, text: str, author: str = "Maria RH\nRecrutadora") -> dict:
    return {"urn": f"urn:li:activity:{urn_id}", "text": text, "author": author}


def test_extracts_job_posts_only():
    posts = [
        make_post("111", "🚀 Estamos contratando! Vaga para Desenvolvedor Fullstack "
                         "Pleno, 100% remoto. Envie seu currículo."),
        make_post("222", "Hoje completei 5 anos de empresa, muito feliz!"),
        make_post("333", "We're hiring a Junior Backend Developer (Node.js)."),
    ]
    jobs = extract_jobs_from_posts(posts, "fullstack")
    assert {j.external_id for j in jobs} == {"111", "333"}
    job = next(j for j in jobs if j.external_id == "111")
    assert job.source == "linkedin_posts"
    assert job.title.startswith("[post] ")
    assert job.company == "Maria RH"
    assert "urn:li:activity:111" in job.url
    assert "contratando" in job.description.lower()


def test_dedups_by_activity_id():
    post = make_post("111", "Vaga para dev backend junior, candidate-se!")
    jobs = extract_jobs_from_posts([post, post], "backend")
    assert len(jobs) == 1


def test_ignores_posts_without_urn():
    posts = [{"urn": "", "text": "Vaga para dev", "author": "X"}]
    assert extract_jobs_from_posts(posts, "dev") == []
