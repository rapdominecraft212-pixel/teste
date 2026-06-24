import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sys
from db import init_db, set_job_processing, set_job_ready
from deliver_job import mark_and_deliver


def simulate(job_id, output_path):
    init_db()

    set_job_processing(job_id)

    set_job_ready(job_id, output_path)
    mark_and_deliver(job_id, output_path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python simulate.py <job_id> <output_path>")
        raise SystemExit(1)

    simulate(sys.argv[1], sys.argv[2])
