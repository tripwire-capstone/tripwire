from typing import Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query, Response

from backend.live import load_live
from eval.storage import connect_database, load_gold, load_report

app = FastAPI(title="Signals evaluation API")


@app.get("/api/results")
def get_results(response: Response, split: Literal["dev", "test"] = "dev"):
    response.headers["Cache-Control"] = "no-store"
    try:
        cases = load_gold(split)
        # Reads cannot seed turns, update scores, or call the classifier.
        with connect_database(read_only=True) as connection:
            return load_report(connection, cases, split)
    except psycopg.errors.UndefinedTable as error:
        raise HTTPException(
            status_code=503,
            detail="Database tables are missing. Apply schema.sql before loading results.",
        ) from error
    except psycopg.Error as error:
        # Do not expose database credentials or connection strings to the browser.
        raise HTTPException(
            status_code=503,
            detail="Cannot reach the database. Check Postgres and DATABASE_URL in .env.",
        ) from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/live")
def get_live(response: Response, limit: int = Query(200, ge=1, le=1000)):
    response.headers["Cache-Control"] = "no-store"
    try:
        # Read-only like /api/results: scores come from classifier.live, never from here.
        with connect_database(read_only=True) as connection:
            return load_live(connection, limit)
    except psycopg.errors.UndefinedTable as error:
        raise HTTPException(
            status_code=503,
            detail="Database tables are missing. Apply schema.sql before loading results.",
        ) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach the database. Check Postgres and DATABASE_URL in .env.",
        ) from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
