from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from sih import entities as E


def test_keywords_normalise_singular_stopwords_alias():
    kw = E.keywords("Show HN: LLM Agents for the Large Language Model era")
    assert "llm agent" in kw and "llm" in kw
    assert "the" not in kw and "show" not in kw
    assert "llm era" in kw  # alias appliqué sur le bigramme "large language model" → jetons séparés
    assert E.keywords("Rust and C++ and C#") >= {"rust", "c++", "c#"}


def test_repos_and_domains():
    assert E.repos("https://github.com/Foo/Bar.git", "see github.com/orgs/x") == {"foo/bar"}
    assert E.normalize_domain("www.Example.com") == "example.com"
    assert E.normalize_domain("github.com") is None
    ents = E.extract("hn", "New tool", "https://github.com/a/b", "github.com", None)
    assert ("repo", "a/b") in ents and not any(k == "domain" for k, _ in ents)
    assert ("repo", "o/r") in E.extract("github", "o/R", "https://github.com/o/R", "github.com", "desc")


def test_aggregate_is_incremental_and_not_double_counted(clean):
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    with clean.begin() as conn:
        sid = conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('rss','u','ai') RETURNING id")).scalar_one()
        for i in range(3):
            conn.execute(
                text("INSERT INTO raw_events (source_id, external_id, title, url, published_at, collected_at) "
                     "VALUES (:s, :e, 'Quantum computing breakthrough', :u, :p, :c)"),
                {"s": sid, "e": str(i), "u": f"https://q.example/{i}", "p": now - timedelta(hours=1), "c": now - timedelta(minutes=5)},
            )
    with clean.begin() as conn:
        assert E.aggregate(conn, now) == 3
    with clean.begin() as conn:
        assert E.aggregate(conn, now) == 0  # rien de neuf
        m = conn.execute(text("SELECT mentions FROM entity_hourly h JOIN entities e ON e.id=h.entity_id "
                              "WHERE e.name='quantum'")).scalar_one()
    assert m == 3  # trois histoires distinctes


def test_old_items_are_stored_but_not_counted_as_activity(clean):
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    with clean.begin() as conn:
        sid = conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('rss','old','ai') RETURNING id")).scalar_one()
        conn.execute(
            text("INSERT INTO raw_events (source_id, external_id, title, published_at, collected_at) "
                 "VALUES (:s,'old','Quantum archive piece',:p,:c)"),
            {"s": sid, "p": datetime(2015, 1, 1, tzinfo=UTC), "c": now - timedelta(minutes=5)},
        )
    with clean.begin() as conn:
        assert E.aggregate(conn, now) == 1  # traité (watermark avancé)
        assert conn.execute(text("SELECT count(*) FROM entity_hourly")).scalar_one() == 0


def test_aggregate_never_skips_a_late_committed_row(clean):
    """Une ligne récente à identifiant plus petit ne doit pas être sautée par le curseur."""
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    with clean.begin() as conn:
        sid = conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('rss','late','ai') RETURNING id")).scalar_one()

        def add(ext, collected):
            conn.execute(
                text("INSERT INTO raw_events (source_id, external_id, title, url, published_at, collected_at) "
                     "VALUES (:s,:e,'Latecomer story',:u,:p,:c)"),
                {"s": sid, "e": ext, "u": f"https://late.example/{ext}", "p": collected, "c": collected},
            )

        add("a", now - timedelta(minutes=5))   # id 1, éligible
        add("b", now - timedelta(seconds=5))   # id 2, trop récent
        add("c", now - timedelta(minutes=5))   # id 3, éligible mais après la ligne trop récente
    with clean.begin() as conn:
        assert E.aggregate(conn, now) == 1  # s'arrête à « b »
    with clean.begin() as conn:
        assert E.aggregate(conn, now + timedelta(minutes=1)) == 2  # « b » puis « c » : rien sauté
        n = conn.execute(text("SELECT mentions FROM entity_hourly h JOIN entities e ON e.id=h.entity_id WHERE e.name='latecomer'")).scalar_one()
    assert n == 3


def test_generic_words_are_not_entities():
    kw = E.keywords("Time to work on your team: the best way to build a company tool")
    assert kw.isdisjoint({"time", "work", "team", "way", "build", "company", "tool"})
    assert "quantum" in E.keywords("Quantum processor announced")  # un terme spécifique reste


def test_story_key_ignores_tracking_and_scheme():
    assert E.story_key("https://www.a.com/x/?utm_source=rss#top", "T") == E.story_key("http://a.com/x", "U")
    assert E.story_key(None, "Big  News!") == E.story_key(None, "big news")


def test_republications_within_a_community_count_once(clean):
    """Un même article repris par 6 flux d'une communauté = 1 histoire, pas 6 mentions."""
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    with clean.begin() as conn:
        for i in range(6):
            sid = conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('rss',:u,'ai') RETURNING id"),
                               {"u": f"feed-{i}"}).scalar_one()
            conn.execute(
                text("INSERT INTO raw_events (source_id, external_id, title, url, published_at, collected_at) "
                     "VALUES (:s,:e,'Frontier model released',:u,:p,:c)"),
                {"s": sid, "e": f"x{i}", "u": "https://news.example/frontier?utm_source=feed", "p": now - timedelta(hours=1),
                 "c": now - timedelta(minutes=5)},
            )
    with clean.begin() as conn:
        E.aggregate(conn, now)
        m = conn.execute(text("SELECT sum(mentions) FROM entity_hourly h JOIN entities e ON e.id=h.entity_id "
                              "WHERE e.name='frontier'")).scalar_one()
    assert m == 1


def _story(conn, sid, ext, url, title, when):
    conn.execute(
        text("INSERT INTO raw_events (source_id, external_id, title, url, published_at, collected_at) "
             "VALUES (:s,:e,:t,:u,:p,:p)"),
        {"s": sid, "e": ext, "t": title, "u": url, "p": when},
    )


def test_syndicated_copies_do_not_trigger_but_distinct_stories_do(clean):
    """6 copies d'un même article dans deux communautés : rien. 10 histoires distinctes : détection."""
    from sih import detect as D

    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    when = now - timedelta(hours=1)
    with clean.begin() as conn:
        srcs = {c: conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('rss',:u,:c) RETURNING id"),
                                {"u": f"f-{c}", "c": c}).scalar_one() for c in ("ai", "security")}
        for sid in srcs.values():
            for i in range(6):
                _story(conn, sid, f"rep{i}", "https://wire.example/zorbix-launch", "Zorbix launch", when)
    with clean.begin() as conn:
        E.aggregate(conn, now)
    with clean.begin() as conn:
        assert D.detect(conn, now, persist=False) == []

    with clean.begin() as conn:
        for c, sid in srcs.items():
            for i in range(10):  # base vide : le plancher MAD exige ~9 histoires sur 6 h pour z ≥ 4
                _story(conn, sid, f"new{i}", f"https://{c}.example/story-{i}", f"Quarkx story {i}", when)
    with clean.begin() as conn:
        E.aggregate(conn, now + timedelta(minutes=1))
    with clean.begin() as conn:
        found = [r.name for r in D.detect(conn, now + timedelta(minutes=1), persist=False)]
    assert "quarkx" in found
