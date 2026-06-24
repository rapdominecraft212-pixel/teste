import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import secrets
from dotenv import load_dotenv
from db import init_db, create_job

load_dotenv()


def create_video_job(chat_id, input_url):
    init_db()
    job_id = secrets.token_urlsafe(16)
    create_job(job_id, chat_id, input_url)
    return job_id


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Uso: python create_job.py <chat_id> <url>")
        raise SystemExit(1)

    job_id = create_video_job(int(sys.argv[1]), sys.argv[2])
    print(f"Tarefa criada: {job_id}")
