import json
import re
import time
import uuid

from app.core.cassandra import get_cassandra_session
from app.core.statements import ArticleStmts
from app.modules.newspaper.article_store import get_article

CORRUPTED_IDS = [
    "21663ec7-82a2-4660-98f7-62b7450434f5",
    "03aaac89-4079-4d75-b637-6406308280c5",
    "82761ff1-519f-4fb8-8a35-5cae143f2e00",
    "80c7bc5a-324c-449e-b288-f7375482cc85",
    "353c11f5-4848-4a7a-8609-474d67688abe",
    "0e442c8f-af20-476c-914d-a181e7cff139",
    "b1d490ea-49aa-4a49-9561-b03ee0ed27e3",
    "0e50a9fe-0013-47ea-ab3f-4a3c0e741138",
    "13693a26-7d62-4d01-bab1-bc74f6ab791e",
    "185531a4-7f75-4d42-8698-2f3640b991c2",
    "065d286b-cefc-4bf1-9708-2314765dd997",
    "23c4fc7c-8e0a-4946-82b8-38e2875e051d",
]

PATTERN = re.compile(r"(.{8,})\1{2,}")


def main() -> None:
    session = get_cassandra_session()

    print("--- step 1: clear the corrupted 'ps' entry for each article ---", flush=True)
    for aid in CORRUPTED_IDS:
        session.execute(ArticleStmts.DELETE_TRANSLATION_LANG, ("ps", uuid.UUID(aid)))
        print(f"  cleared ps for {aid}", flush=True)

    print("\n--- step 2: re-enqueue ps via the celery task (now DeepSeek-routed) ---", flush=True)
    from app.celery_app import celery_app

    task_ids = {}
    for aid in CORRUPTED_IDS:
        r = celery_app.send_task("app.tasks.newspaper.translate_article_batch", args=[aid, ["ps"]])
        task_ids[aid] = r.id
        print(f"  enqueued {aid} -> task {r.id}", flush=True)

    print("\n--- step 3: poll until all 12 tasks are terminal ---", flush=True)
    from celery.result import AsyncResult

    pending = dict(task_ids)
    results = {}
    while pending:
        time.sleep(5)
        for aid, tid in list(pending.items()):
            r = AsyncResult(tid, app=celery_app)
            if r.state in ("SUCCESS", "FAILURE"):
                results[aid] = (r.state, r.result)
                del pending[aid]
                print(f"  {aid}: {r.state} {r.result}", flush=True)
        print(f"  ... {len(pending)} still pending", flush=True)

    print("\n--- step 4: verify each new ps translation is clean ---", flush=True)
    for aid in CORRUPTED_IDS:
        a = get_article(aid)
        tr = a.translations or {}
        if "ps" not in tr:
            print(f"  {aid}: NO PS TRANSLATION -- FAILED TO REGENERATE")
            continue
        ps = json.loads(tr["ps"])
        body = ps.get("body", "")
        m = PATTERN.search(body) or PATTERN.search(ps.get("title", ""))
        status = "STILL CORRUPTED" if m else "CLEAN"
        print(f"  {aid}: {status} (body_len={len(body)}, english_len={len(a.body)})")
        if m:
            print(f"      match: {m.group(0)[:150]!r}")

    print("\nALL DONE", flush=True)


if __name__ == "__main__":
    main()
