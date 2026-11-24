"""Job sources. Every fetcher returns normalised job dicts with the keys in db.JOB_COLS."""
from . import (adzuna, arbeitnow, ashby, greenhouse, himalayas, hn_hiring, jobicy, jooble, lever,
               personio, recruitee, remotive, remoteok, weworkremotely, workable)

# ATS boards that can be read directly by company slug: fetch_board(slug) -> list[dict] | None
BOARDS = {
    "greenhouse": greenhouse.fetch_board,
    "lever": lever.fetch_board,
    "ashby": ashby.fetch_board,
    "workable": workable.fetch_board,
    "recruitee": recruitee.fetch_board,
    "personio": personio.fetch_board,
}
AGGREGATORS = {
    "remotive": remotive.fetch,
    "arbeitnow": arbeitnow.fetch,
    "remoteok": remoteok.fetch,
    "himalayas": himalayas.fetch,
    "jobicy": jobicy.fetch,
    "weworkremotely": weworkremotely.fetch,
    "hn_hiring": hn_hiring.fetch,
    "adzuna": adzuna.fetch,
    "jooble": jooble.fetch,
}
